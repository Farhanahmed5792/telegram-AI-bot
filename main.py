from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types
import asyncio
import traceback
import requests
import urllib.parse
from config import (
    TELEGRAM_TOKEN, GEMINI_API_KEY, POLLINATIONS_KEY, ADMIN_ID, UPI_ID, PRICE_PER_EDIT,
    OPENROUTER_API_KEY, OPENROUTER_MODEL
)
from bot_menus import (
    get_main_menu, get_core_menu, get_memory_menu, get_image_menu,
    get_internet_menu, get_video_menu, get_settings_menu, get_persistent_menu,
    get_document_menu, get_audio_menu
)

# ---------------- GEMINI CLIENT (TEXT + VISION/DOCUMENT ke liye) ----------------
client = genai.Client(api_key=GEMINI_API_KEY)
TEXT_MODEL = "gemini-2.5-flash"

# ---------------- OPENROUTER CONFIG (Gemini ka quota/token khatam hone par fallback) ----------------
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

user_modes = {}  # Yahan user_id aur uska active mode save hoga
user_memory = {}  # Har user ki "remember" ki hui facts yahan store hongi
user_pending_photo_url = {}  # img_edit / video_photo2video ke liye uploaded photo ka Telegram URL
user_pending_document = {}  # document_* mode ke liye uploaded image/file ke bytes+mime store hote hain
user_edit_credits = {}  # Har user ke paas kitne paid photo-edit credits bache hain
user_last_image = {}  # Save button ke liye - last generated/edited image ke bytes
user_last_video = {}  # Save button ke liye - last generated video ke bytes
user_last_audio = {}  # last generated audio ke bytes (abhi koi save button nahi hai iske liye)
FREE_EDITS = 0  # Naye user ko itne edits free milenge


# ---------------- AI FALLBACK LOOP (Gemini -> OpenRouter) ----------------
def is_quota_or_limit_error(e) -> bool:
    """Check karta hai ki error Gemini ke token/quota/rate-limit khatam hone ki wajah se hai ya nahi."""
    msg = str(e).lower()
    keywords = [
        "quota", "rate limit", "rate_limit", "resource_exhausted",
        "429", "exceeded", "limit reached", "insufficient", "billing"
    ]
    return any(k in msg for k in keywords)


def call_gemini(prompt: str) -> str:
    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=prompt,
    )
    return response.text


def call_openrouter(prompt: str, use_internet: bool = False, model: str = None) -> str:
    """
    OpenRouter ko call karta hai. use_internet=True hone par model name ke aage
    ':online' laga dete hain - isse OpenRouter khud web search plugin use karke
    live/current data ke saath jawab deta hai.
    """
    model_name = model or OPENROUTER_MODEL
    if use_internet and not model_name.endswith(":online"):
        model_name = f"{model_name}:online"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


CURRENT_INFO_KEYWORDS = [
    # English
    "today", "current", "latest", "recent", "now", "date", "when is", "when will",
    "news", "price", "result", "release date", "who is", "who won", "score",
    "update", "this year", "2026", "2025",
    # Hinglish/Hindi
    "aaj", "abhi", "kab hai", "kab hoga", "kab aayega", "naya", "nayi",
    "taaza", "kitna hai", "kya hai abhi", "abhi tak",
]


def needs_internet(text: str) -> bool:
    """Check karta hai ki query me kuch aisa hai jo sirf live/current internet data se
    hi sahi answer ho sakta hai (date, news, price, latest info waghera)."""
    lowered = text.lower()
    return any(keyword in lowered for keyword in CURRENT_INFO_KEYWORDS)


def generate_ai_response(prompt: str, use_internet: bool = False):
    """
    MAIN LOOP:
    - Agar internet/search mode hai -> seedha OpenRouter (:online) use karo, kyunki
      Gemini ke paas live web data nahi hai.
    - Warna pehle Gemini try karo. Gemini ka token/quota khatam ho jaye (ya koi bhi
      error aaye) toh loop khud OpenRouter pe switch ho jaata hai, taaki user ko
      bina rukawat ke jawab milta rahe.
    Return: (reply_text, source) jaha source = "gemini" ya "openrouter"
    """
    if use_internet:
        reply = call_openrouter(prompt, use_internet=True)
        return reply, "openrouter"

    try:
        reply = call_gemini(prompt)
        return reply, "gemini"
    except Exception as e:
        reason = "quota/limit khatam" if is_quota_or_limit_error(e) else "unexpected error"
        print(f"GEMINI FAILED ({reason}): {e} -> switching to OpenRouter")
        reply = call_openrouter(prompt, use_internet=False)
        return reply, "openrouter"


# ---------------- DOCUMENT / IMAGE ANALYSIS (Gemini Vision) ----------------
def analyze_document(file_bytes: bytes, mime_type: str, instruction: str) -> str:
    """Gemini ko image/PDF ke bytes + instruction dono ek saath bhejta hai (multimodal)."""
    part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=[part, instruction],
    )
    return response.text


# ---------------- AUDIO (Pollinations Text-to-Speech) ----------------
def generate_speech(text: str, voice: str = "alloy") -> bytes:
    url = f"https://text.pollinations.ai/{urllib.parse.quote(text)}"
    params = {"model": "openai-audio", "voice": voice}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.content


# ---------------- VIDEO (Pollinations Video Generation) ----------------
def generate_video(prompt: str) -> bytes:
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://gen.pollinations.ai/video/{encoded_prompt}"
    headers = {"Authorization": f"Bearer {POLLINATIONS_KEY}"}
    params = {"model": "veo", "width": "1024"}
    resp = requests.get(url, headers=headers, params=params, timeout=600)
    resp.raise_for_status()
    return resp.content


def generate_video_from_image(prompt: str, image_url: str) -> bytes:
    """
    Photo-to-Video: keyframe image ke saath video banata hai.
    NOTE: Pollinations ka keyframe param naam future me badal sakta hai -
    agar ye error de, toh unke docs (gen.pollinations.ai/docs) check karke
    'image' param ka sahi naam update kar dena.
    """
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://gen.pollinations.ai/video/{encoded_prompt}"
    headers = {"Authorization": f"Bearer {POLLINATIONS_KEY}"}
    params = {"model": "veo", "width": "1024", "image": image_url}
    resp = requests.get(url, headers=headers, params=params, timeout=600)
    resp.raise_for_status()
    return resp.content


# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_modes[update.effective_user.id] = 'main_menu'
    # Ye persistent "🏠 Main Menu" button hamesha niche (keyboard area) me dikhta rahega,
    # chahe chat me jitni bhi baatein ho jayein, ye scroll ho kar upar nahi jaata.
    await update.message.reply_text(
        "🤖 **AI OS is Online**",
        reply_markup=get_persistent_menu(),
        parse_mode='Markdown'
    )
    await update.message.reply_text(
        "Select an option below:",
        reply_markup=get_main_menu()
    )


# ---------------- BUTTON HANDLER ----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    try:
        await query.answer()
    except Exception:
        pass

    # Agar same button dobara dabaya (same content) toh Telegram error deta hai - usse ignore karenge
    async def safe_edit(text, markup=None, **kwargs):
        try:
            await query.edit_message_text(text, reply_markup=markup, **kwargs)
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"BUTTON EDIT ERROR: {e}")

    if query.data == 'main_menu':
        user_modes[user_id] = 'main_menu'
        await safe_edit("🤖 **AI OS Main Menu**", get_main_menu(), parse_mode='Markdown')

    elif query.data == 'core':
        await safe_edit("🟢 **Select Mode:**", get_core_menu(), parse_mode='Markdown')

    elif query.data == 'mode_chat':
        user_modes[user_id] = 'chat'
        await safe_edit("💬 **Chat Mode Active!**\nAb aap mujhse baat kar sakte hain.", get_core_menu())

    elif query.data == 'mode_teach':
        user_modes[user_id] = 'teach'
        await safe_edit("👨‍🏫 **Teaching Mode Active!**\nMain aapko kuch bhi sikha sakta hoon.", get_core_menu())

    elif query.data == 'mode_write':
        user_modes[user_id] = 'write'
        await safe_edit("✍️ **Writing Mode Active!**\nKya likhna hai?", get_core_menu())

    elif query.data == 'mode_coding':
        user_modes[user_id] = 'coding'
        await safe_edit("💻 **Coding Mode Active!**\nCode bhejo ya task batao.", get_core_menu())

    elif query.data == 'memory':
        await safe_edit("🧠 **Memory Settings:**\nSelect an action:", get_memory_menu(), parse_mode='Markdown')

    elif query.data == 'mem_remember':
        user_modes[user_id] = 'remember'
        await safe_edit("📝 Kya yaad rakhna hai? Mujhe likh kar bhejo.", get_memory_menu())

    elif query.data == 'mem_forget':
        user_memory[user_id] = []
        await safe_edit("🗑️ **Saari yaad rakhi hui baatein delete kar di gayi hain.**", get_memory_menu(), parse_mode='Markdown')

    elif query.data == 'mem_notes':
        notes = user_memory.get(user_id, [])
        if not notes:
            text = "📝 Abhi tak kuch yaad nahi rakha gaya hai."
        else:
            lines = "\n".join(f"{i+1}. {n}" for i, n in enumerate(notes))
            text = f"📝 **Yaad rakhi hui baatein:**\n{lines}"
        await safe_edit(text, get_memory_menu(), parse_mode='Markdown')

    elif query.data == 'image':
        await safe_edit("🎨 **Image Studio:**\nSelect an action:", get_image_menu(), parse_mode='Markdown')

    elif query.data == 'img_generate':
        user_modes[user_id] = 'img_generate'
        await safe_edit("🎨 Kya image banani hai? Description do.", get_image_menu())

    elif query.data == 'img_edit':
        user_modes[user_id] = 'img_edit'
        await safe_edit("🎨 **Image Editor**\nPhoto bhejo jise edit karna hai.", get_image_menu())

    elif query.data == 'img_save':
        img_bytes = user_last_image.get(user_id)
        if img_bytes is None:
            await query.message.reply_text("💾 Abhi tak koi image generate/edit nahi hui hai jise save karu.")
        else:
            with open(f"save_image_{user_id}.png", "wb") as f:
                f.write(img_bytes)
            await query.message.reply_document(
                document=open(f"save_image_{user_id}.png", "rb"),
                filename="saved_image.png"
            )
            await query.message.reply_text("✅ Image document ke roop me bhej di - ab apne phone me save kar lo.")

    elif query.data == 'internet':
        await safe_edit("🌐 **Internet Access:**\nSearch anything on web.", get_internet_menu(), parse_mode='Markdown')

    elif query.data == 'internet_search':
        user_modes[user_id] = 'search'
        await safe_edit("🌐 **Search Mode Active!**\nKya search karu? (Type your query below)", get_internet_menu())

    elif query.data == 'audio':
        await safe_edit("🎵 **Audio Studio:**\nSelect an action:", get_audio_menu(), parse_mode='Markdown')

    elif query.data == 'audio_tts':
        user_modes[user_id] = 'audio_tts'
        await safe_edit("🎤 Kya bolwana hai? Text likh do, main audio bana dunga.", get_audio_menu())

    elif query.data == 'document':
        await safe_edit("📄 **Document Tools:**\nSelect an action:", get_document_menu(), parse_mode='Markdown')

    elif query.data == 'document_image_reader':
        user_modes[user_id] = 'document_image_reader'
        await safe_edit("🖼️ **Image Reader**\nEk image bhejo jise AI padhega/analyze karega.", get_document_menu())

    elif query.data == 'document_file_pdf_reader':
        user_modes[user_id] = 'document_file_pdf_reader'
        await safe_edit("📄 **File & PDF Reader**\nEk PDF ya document bhejo.", get_document_menu())

    elif query.data == 'document_data_extracter':
        user_modes[user_id] = 'document_data_extracter'
        await safe_edit("📊 **Data Extracter**\nImage ya file bhejo, AI usme se important data (numbers, dates, names) nikaal dega.", get_document_menu())

    elif query.data == 'document_save':
        pending = user_pending_document.get(user_id)
        if pending is None:
            await query.message.reply_text("💾 Abhi tak koi image/file upload nahi hui hai jise save karu.")
        else:
            mime = pending["mime"]
            ext = "pdf" if "pdf" in mime else ("png" if "image" in mime else "bin")
            filename = f"document_{user_id}.{ext}"
            with open(filename, "wb") as f:
                f.write(pending["bytes"])
            await query.message.reply_document(document=open(filename, "rb"), filename=f"saved_document.{ext}")
            await query.message.reply_text("✅ Document/Image save ho gaya - ab apne phone me save kar lo.")

    elif query.data == 'video':
        await safe_edit("🎥 **Video Suite:**\nSelect an action:", get_video_menu(), parse_mode='Markdown')

    elif query.data == 'video_generate':
        user_modes[user_id] = 'video_generate'
        await safe_edit("🎥 **Video Generation**\nVideo ka description do.", get_video_menu())

    elif query.data == 'video_photo2video':
        user_modes[user_id] = 'video_photo2video'
        await safe_edit("🎥 **Photo to Video**\nEk photo upload karo.", get_video_menu())

    elif query.data == 'video_edit':
        user_modes[user_id] = 'video_edit'
        await safe_edit(
            "😎 **Video Edit**\nYe feature abhi available nahi hai - Pollinations ke paas video-edit ka public API nahi hai. Jald hi add karenge.",
            get_video_menu()
        )

    elif query.data == 'video_save':
        vid_bytes = user_last_video.get(user_id)
        if vid_bytes is None:
            await query.message.reply_text("💾 Abhi tak koi video generate nahi hui hai jise save karu.")
        else:
            with open(f"save_video_{user_id}.mp4", "wb") as f:
                f.write(vid_bytes)
            await query.message.reply_document(
                document=open(f"save_video_{user_id}.mp4", "rb"),
                filename="saved_video.mp4"
            )
            await query.message.reply_text("✅ Video document ke roop me bhej di - ab apne phone me save kar lo.")

    elif query.data == 'settings':
        await safe_edit("⚙️ **Settings**\nSelect an option:", get_settings_menu(), parse_mode='Markdown')

    elif query.data == 'settings_language':
        await safe_edit(
            "🌐 **Language**\nAbhi ke liye Hinglish (Hindi + English) default hai.\nAage aap yaha language switch kar sakenge.",
            get_settings_menu()
        )

    elif query.data == 'settings_credits':
        credits = user_edit_credits.get(user_id, FREE_EDITS)
        await safe_edit(f"💳 **Aapke Credits**\nPhoto-edit credits bache hain: {credits}", get_settings_menu())

    elif query.data == 'settings_clear_memory':
        user_memory[user_id] = []
        await safe_edit("🧠 **Memory Cleared**\nAapki saari yaad rakhi hui baatein delete kar di gayi hain.", get_settings_menu())

    elif query.data == 'settings_about':
        await safe_edit(
            "ℹ️ **About**\nYe bot Gemini + OpenRouter se chalta hai, image/video/audio generate aur edit kar sakta hai, document/PDF padh sakta hai, aur internet se live jankari bhi de sakta hai.",
            get_settings_menu()
        )


# ---------------- ADMIN: Manually credits add karne ke liye ----------------
async def addcredits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ye command sirf admin use kar sakta hai.")
        return

    try:
        target_user_id = int(context.args[0])
        amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /addcredits <user_id> <amount>\nExample: /addcredits 8855238317 5")
        return

    current = user_edit_credits.get(target_user_id, FREE_EDITS)
    user_edit_credits[target_user_id] = current + amount

    await update.message.reply_text(f"✅ User {target_user_id} ko {amount} credits mil gaye. Total: {user_edit_credits[target_user_id]}")

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"🎉 Aapko {amount} photo-edit credits mil gaye hain! Ab total: {user_edit_credits[target_user_id]}"
        )
    except Exception as e:
        print(f"Could not notify user: {e}")


# ---------------- PHOTO HANDLER (Image Edit / Photo2Video / Document Image ke liye) ----------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode = user_modes.get(user_id, 'chat')

    # Telegram se photo ka direct public URL milta hai
    photo_file = await update.message.photo[-1].get_file()
    file_path = photo_file.file_path
    if file_path.startswith("http"):
        full_url = file_path
    else:
        full_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"

    if mode == 'img_edit':
        user_pending_photo_url[user_id] = full_url
        await update.message.reply_text("📸 Photo mil gayi! Ab bata do kya change karna hai. (jaise: 'background blue kar do')")
        return

    if mode == 'video_photo2video':
        user_pending_photo_url[user_id] = full_url
        await update.message.reply_text("📸 Photo mil gayi! Ab bata do video me kya movement/action chahiye.")
        return

    if mode in ('document_image_reader', 'document_data_extracter'):
        try:
            img_response = await asyncio.to_thread(requests.get, full_url, timeout=60)
            img_response.raise_for_status()
            user_pending_document[user_id] = {"bytes": img_response.content, "mime": "image/jpeg", "kind": "image"}
            await update.message.reply_text("📸 Image mil gayi! Ab bata do isme kya karna hai (jaise: 'ye kya hai', 'is se text nikaalo').")
        except Exception as e:
            print(f"PHOTO DOWNLOAD ERROR: {e}")
            await update.message.reply_text("⚠️ Photo download karte waqt error aa gaya. Dobara try karo.")
        return

    await update.message.reply_text(
        "📸 Photo mil gayi, lekin abhi koi aisa mode active nahi hai jo photo use kare.\n"
        "Pehle 🎨 Image Studio -> Edit, 🎥 Video -> Photo to Video, ya 📄 Document -> Image Reader select karo."
    )


# ---------------- DOCUMENT/FILE HANDLER (PDF/File Reader aur Data Extracter ke liye) ----------------
async def document_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    mode = user_modes.get(user_id, 'chat')

    if mode not in ('document_file_pdf_reader', 'document_data_extracter'):
        await update.message.reply_text(
            "📄 File mil gayi, lekin abhi 'Document' mode active nahi hai.\n"
            "Pehle 📄 Document -> File & PDF Reader (ya Data Extracter) select karo, phir file bhejo."
        )
        return

    doc = update.message.document
    try:
        tg_file = await doc.get_file()
        file_path = tg_file.file_path
        full_url = file_path if file_path.startswith("http") else f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"

        file_response = await asyncio.to_thread(requests.get, full_url, timeout=60)
        file_response.raise_for_status()

        mime_type = doc.mime_type or "application/pdf"
        user_pending_document[user_id] = {"bytes": file_response.content, "mime": mime_type, "kind": "file"}
        await update.message.reply_text("📄 File mil gayi! Ab bata do isme kya karna hai (jaise: 'summary do', 'important points nikaalo').")
    except Exception as e:
        print(f"DOCUMENT DOWNLOAD ERROR: {e}")
        traceback.print_exc()
        await update.message.reply_text("⚠️ File download karte waqt error aa gaya. Dobara try karo.")


# ---------------- CHAT / TEXT HANDLER ----------------
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    mode = user_modes.get(user_id, 'chat')

    print(f"DEBUG: User {user_id} | Mode: {mode} | Message: {user_text}")

    # ---- Persistent "🏠 Main Menu" button (hamesha keyboard me visible rehta hai) ----
    if user_text == "🏠 Main Menu":
        user_modes[user_id] = 'main_menu'
        await update.message.reply_text(
            "🤖 **AI OS Main Menu**",
            reply_markup=get_main_menu(),
            parse_mode='Markdown'
        )
        return

    # ---- Remember Mode ----
    if mode == 'remember':
        user_memory.setdefault(user_id, []).append(user_text)
        await update.message.reply_text(f"✅ Yaad rakh liya: \"{user_text}\"")
        return

    # ---- Search Mode (OpenRouter ke ':online' model se real internet data ke saath) ----
    if mode == 'search':
        await update.message.reply_text("🔎 Internet pe search kar raha hoon, thoda ruko...")
        try:
            reply_text, source = generate_ai_response(user_text, use_internet=True)
            print(f"DEBUG SEARCH SOURCE: {source}")
            if len(reply_text) > 4000:
                for i in range(0, len(reply_text), 4000):
                    await update.message.reply_text(reply_text[i:i+4000])
            else:
                await update.message.reply_text(reply_text)
        except Exception as e:
            print(f"SEARCH ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Internet search karte waqt error aa gaya. Dobara try karo.")
        return

    # ---- Image Generate (FREE - Pollinations.ai, no billing needed) ----
    if mode == 'img_generate':
        await update.message.reply_text("🎨 Image ban rahi hai, thoda ruko...")
        try:
            encoded_prompt = urllib.parse.quote(user_text)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&key={POLLINATIONS_KEY}"

            img_response = await asyncio.to_thread(requests.get, image_url, timeout=60)

            if img_response.status_code == 200:
                user_last_image[user_id] = img_response.content
                with open("temp_image.png", "wb") as f:
                    f.write(img_response.content)
                await update.message.reply_photo(photo=open("temp_image.png", "rb"))
            else:
                await update.message.reply_text("⚠️ Image nahi ban payi. Dobara try karo.")
        except Exception as e:
            print(f"IMAGE ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Image generate karte waqt error aa gaya. Dobara try karo.")
        return

    # ---- Image Edit (Paid - FREE_EDITS free, phir credits chahiye) ----
    if mode == 'img_edit':
        pending_url = user_pending_photo_url.get(user_id)
        if pending_url is None:
            await update.message.reply_text("📸 Pehle ek photo bhejo, uske baad batana kya edit karna hai.")
            return

        credits = user_edit_credits.get(user_id, FREE_EDITS)

        if credits <= 0:
            await update.message.reply_text(
                f"💳 **Aapke free edits khatam ho gaye!**\n\n"
                f"Ek edit ke liye ₹{PRICE_PER_EDIT} lagte hain.\n\n"
                f"👉 Is UPI ID pe payment karo: `{UPI_ID}`\n"
                f"👉 Payment ka screenshot yahan bhejo\n"
                f"👉 Verify hote hi credits add ho jayenge\n\n"
                f"Jitna chaho utna top-up kara sakte ho (jaise ₹{PRICE_PER_EDIT*5} = 5 edits)",
                parse_mode='Markdown'
            )
            return

        await update.message.reply_text("🎨 Photo edit ho rahi hai, thoda ruko...")
        try:
            edit_endpoint = "https://gen.pollinations.ai/v1/images/edits"
            headers = {
                "Authorization": f"Bearer {POLLINATIONS_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "image": pending_url,
                "prompt": user_text,
                "model": "kontext",
                "size": "1024x1024",
            }
            print(f"DEBUG EDIT REQUEST: {payload}")

            api_response = await asyncio.to_thread(
                requests.post, edit_endpoint, headers=headers, json=payload, timeout=90
            )
            print(f"DEBUG EDIT STATUS: {api_response.status_code}")

            if api_response.status_code == 200:
                result = api_response.json()
                data_item = result.get("data", [{}])[0]

                if "b64_json" in data_item:
                    import base64
                    img_bytes = base64.b64decode(data_item["b64_json"])
                    user_last_image[user_id] = img_bytes
                    with open("temp_edited_image.png", "wb") as f:
                        f.write(img_bytes)
                    await update.message.reply_photo(photo=open("temp_edited_image.png", "rb"))
                    del user_pending_photo_url[user_id]
                    user_edit_credits[user_id] = credits - 1
                    await update.message.reply_text(f"✅ Bache hue credits: {user_edit_credits[user_id]}")
                elif "url" in data_item:
                    img_data = await asyncio.to_thread(requests.get, data_item["url"], timeout=60)
                    user_last_image[user_id] = img_data.content
                    with open("temp_edited_image.png", "wb") as f:
                        f.write(img_data.content)
                    await update.message.reply_photo(photo=open("temp_edited_image.png", "rb"))
                    del user_pending_photo_url[user_id]
                    user_edit_credits[user_id] = credits - 1
                    await update.message.reply_text(f"✅ Bache hue credits: {user_edit_credits[user_id]}")
                else:
                    print(f"DEBUG EDIT UNEXPECTED RESPONSE: {result}")
                    await update.message.reply_text("⚠️ Edit response samajh nahi aaya. Dobara try karo.")
            else:
                print(f"DEBUG EDIT ERROR BODY: {api_response.text[:500]}")
                await update.message.reply_text("⚠️ Edit nahi ho paya. Instruction thoda alag try karo.")
        except Exception as e:
            print(f"IMAGE EDIT ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Image edit karte waqt error aa gaya. Dobara try karo.")
        return

    # ---- Audio Text-to-Speech (Pollinations) ----
    if mode == 'audio_tts':
        await update.message.reply_text("🎵 Audio ban rahi hai, thoda ruko...")
        try:
            audio_bytes = await asyncio.to_thread(generate_speech, user_text)
            user_last_audio[user_id] = audio_bytes
            with open("temp_audio.mp3", "wb") as f:
                f.write(audio_bytes)
            await update.message.reply_voice(voice=open("temp_audio.mp3", "rb"))
        except Exception as e:
            print(f"AUDIO ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Audio banate waqt error aa gaya. Dobara try karo.")
        return

    # ---- Video Generate (Pollinations - text-to-video) ----
    if mode == 'video_generate':
        await update.message.reply_text("🎥 Video ban rahi hai, isme kaafi time (2-8 minute) lag sakta hai, ruko...")
        try:
            video_bytes = await asyncio.to_thread(generate_video, user_text)
            user_last_video[user_id] = video_bytes
            with open("temp_video.mp4", "wb") as f:
                f.write(video_bytes)
            await update.message.reply_video(video=open("temp_video.mp4", "rb"))
        except requests.exceptions.Timeout:
            await update.message.reply_text("⏳ Video banane me bahut zyada time lag raha hai (server busy hai). Thodi der baad phir try karo, ya description chota/simple rakho.")
        except Exception as e:
            print(f"VIDEO ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Video banate waqt error aa gaya. Dobara try karo.")
        return

    # ---- Video Photo-to-Video (Pollinations keyframe) ----
    if mode == 'video_photo2video':
        pending_url = user_pending_photo_url.get(user_id)
        if pending_url is None:
            await update.message.reply_text("📸 Pehle ek photo bhejo, uske baad batana kya movement/action chahiye.")
            return
        await update.message.reply_text("🎥 Photo ko video me convert kar raha hoon, isme kaafi time (2-8 minute) lag sakta hai, ruko...")
        try:
            video_bytes = await asyncio.to_thread(generate_video_from_image, user_text, pending_url)
            user_last_video[user_id] = video_bytes
            with open("temp_video.mp4", "wb") as f:
                f.write(video_bytes)
            await update.message.reply_video(video=open("temp_video.mp4", "rb"))
            del user_pending_photo_url[user_id]
        except requests.exceptions.Timeout:
            await update.message.reply_text("⏳ Video banane me bahut zyada time lag raha hai (server busy hai). Thodi der baad phir try karo.")
        except Exception as e:
            print(f"PHOTO2VIDEO ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Photo-to-video banate waqt error aa gaya. Dobara try karo.")
        return

    # ---- Video Edit (abhi placeholder - Pollinations ke paas public video-edit API nahi hai) ----
    if mode == 'video_edit':
        await update.message.reply_text("😎 Video Edit abhi implement nahi hua - Pollinations ke paas iske liye public API nahi hai.")
        return

    # ---- Document / Image Reader / Data Extracter (Gemini Vision multimodal) ----
    if mode in ('document_image_reader', 'document_file_pdf_reader', 'document_data_extracter'):
        pending = user_pending_document.get(user_id)
        if pending is None:
            await update.message.reply_text("📄 Pehle image ya file bhejo, uske baad batana kya karna hai.")
            return

        await update.message.reply_text("📄 Document analyze ho raha hai, thoda ruko...")
        try:
            instruction = user_text
            if mode == 'document_data_extracter':
                instruction = (
                    f"Is file/image se important data (jaise numbers, dates, names, key facts, tables) "
                    f"nikaal kar clearly points me do. User ka extra instruction: {user_text}"
                )

            reply_text = await asyncio.to_thread(
                analyze_document, pending["bytes"], pending["mime"], instruction
            )

            if len(reply_text) > 4000:
                for i in range(0, len(reply_text), 4000):
                    await update.message.reply_text(reply_text[i:i+4000])
            else:
                await update.message.reply_text(reply_text)
        except Exception as e:
            print(f"DOCUMENT ANALYZE ERROR: {e}")
            traceback.print_exc()
            await update.message.reply_text("⚠️ Document analyze karte waqt error aa gaya. Dobara try karo.")
        return

    # ---- Default: Gemini AI se chat/teach/write/coding sab handle (token khatam -> OpenRouter loop) ----
    try:
        prompt = user_text
        if mode == 'teach':
            prompt = f"Explain this like a teacher, simply and clearly: {user_text}"
        elif mode == 'write':
            prompt = f"Write content for this request: {user_text}"
        elif mode == 'coding':
            prompt = f"Help with this coding task, give code with explanation: {user_text}"

        auto_internet = needs_internet(user_text)
        reply_text, source = generate_ai_response(prompt, use_internet=auto_internet)
        print(f"DEBUG AI SOURCE: {source} | auto_internet: {auto_internet}")

        if len(reply_text) > 4000:
            for i in range(0, len(reply_text), 4000):
                await update.message.reply_text(reply_text[i:i+4000])
        else:
            await update.message.reply_text(reply_text)

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        await update.message.reply_text("⚠️ Kuch error aa gaya AI response lete waqt. Dobara try karo.")


# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addcredits", addcredits))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == '__main__':
    main()
