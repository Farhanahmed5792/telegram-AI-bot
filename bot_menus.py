from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🟢 Core AI", callback_data='core'), InlineKeyboardButton("📹 Video", callback_data='video')],
        [InlineKeyboardButton("🧠 Memory", callback_data='memory'), InlineKeyboardButton("🎨 Image", callback_data='image')],
        [InlineKeyboardButton("🎵 Audio", callback_data='audio'), InlineKeyboardButton("🌍 Internet", callback_data='internet')],
        [InlineKeyboardButton("📄 Document", callback_data='document'), InlineKeyboardButton("⚙️ Settings", callback_data='settings')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_core_menu():
    keyboard = [
        [InlineKeyboardButton("💬 Chat", callback_data='mode_chat'), InlineKeyboardButton("👨‍🏫 Teach", callback_data='mode_teach')],
        [InlineKeyboardButton("✍️ Write", callback_data='mode_write'), InlineKeyboardButton("💻 Coding", callback_data='mode_coding')],
        [InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_memory_menu():
    keyboard = [
        [InlineKeyboardButton("🧠 Remember", callback_data='mem_remember'),
         InlineKeyboardButton("🗑️ Forget", callback_data='mem_forget')],
        [InlineKeyboardButton("📝 Notes", callback_data='mem_notes'),
         InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_image_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Generate", callback_data='img_generate'),
         InlineKeyboardButton("🌹 Edit", callback_data='img_edit')],
        [InlineKeyboardButton("💾 Save", callback_data='img_save'),
         InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_internet_menu():
    keyboard = [
        [InlineKeyboardButton("search anything", callback_data='internet_search'),
         InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_video_menu():
    keyboard = [
        [InlineKeyboardButton("🎨 Generate", callback_data='video_generate'),
         InlineKeyboardButton("📸 photo2video", callback_data='video_photo2video')],
        [InlineKeyboardButton("😎 Edit", callback_data='video_edit'),
         InlineKeyboardButton("💾 Save", callback_data='video_save')],
        [InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_settings_menu():
    keyboard = [
        [InlineKeyboardButton("🌐 Language", callback_data='settings_language')],
        [InlineKeyboardButton("💳 Check Credits", callback_data='settings_credits')],
        [InlineKeyboardButton("🧠 Clear Memory", callback_data='settings_clear_memory')],
        [InlineKeyboardButton("ℹ️ About", callback_data='settings_about')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='main_menu')],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_document_menu():
    keyboard = [
        [InlineKeyboardButton("🖼️ Image Reader", callback_data='document_image_reader'),
         InlineKeyboardButton("📄 File & PDF Reader", callback_data='document_file_pdf_reader')],
        [InlineKeyboardButton("📊 Data Extracter", callback_data='document_data_extracter'),
         InlineKeyboardButton("💾 Save", callback_data='img_save')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='main_menu')],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_audio_menu():
    keyboard = [
        [InlineKeyboardButton("🎤 Text-to-Speech", callback_data='audio_tts')],
        [InlineKeyboardButton("⬅ Back", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_persistent_menu():
    """
    Ye ek Reply Keyboard hai (inline button nahi) - isliye ye hamesha
    message box ke bilkul upar/bottom me fixed rehta hai, chat scroll hone
    par bhi ye gayab nahi hota, jaise poll/location button hote hain.
    """
    keyboard = [["🏠 Main Menu"]]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,   # button ka size chota/fit rakhta hai
        is_persistent=True      # hamesha visible rakhta hai
    )
