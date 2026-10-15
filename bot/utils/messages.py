"""User-facing messages (Persian)."""

from __future__ import annotations

WELCOME = (
    "سلام! 👋\n"
    "به ربات Grabora خوش آمدید.\n"
    "لینک ویدیو یا آهنگ مورد نظر را ارسال کنید تا دانلود شود."
)

HELP = (
    "راهنما:\n"
    "• لینک یک ویدیو یا آهنگ را ارسال کنید.\n"
    "• کیفیت دلخواه را از دکمه‌ها انتخاب کنید.\n"
    "• برای مشاهده اشتراک: /subscription\n"
)

SUBSCRIPTION_INFO = "اشتراک فعلی: {tier}\nدانلود امروز: {used}/{limit}"

DAILY_LIMIT_REACHED = (
    "امروز به حد مجاز دانلود رسیدید. "
    "برای دانلود بیشتر اشتراک تهیه کنید."
)

BANNED = "دسترسی شما به ربات محدود شده است."

PROCESSING = "در حال پردازش... لطفاً صبر کنید."

DOWNLOAD_FAILED = "دانلود ناموفق بود. لطفاً دوباره تلاش کنید."

FILE_TOO_LARGE = "حجم فایل بیش از حد مجاز است."

PLAYLIST_NOTICE = (
    "لینک پلی‌لیست شناسایی شد. فقط اولین ویدیو دانلود می‌شود."
)

UPGRADE_PROMPT = (
    "این کیفیت فقط برای کاربران اشتراک‌دار است. "
    "برای ارتقا اشتراک تهیه کنید."
)

UPGRADE_FOR_QUALITY = (
    "🔒 کیفیت انتخاب‌شده برای اشتراک رایگان در دسترس نیست.\n"
    "کیفیت‌های 720p و 1080p فقط برای کاربران اشتراک‌دار فعال است "
    "و کیفیت 2K نیازمند افزونه 2K است.\n"
    "🔓 تهیه اشتراک: /subscription"
)

UPGRADE_FOR_SIZE = (
    "📦 حجم این فایل حدوداً {size} مگابایت است و بیش از حد مجاز "
    "برای کاربران رایگان ({limit} مگابایت) می‌باشد.\n"
    "با تهیه اشتراک می‌توانید فایل‌های تا {paid_limit} مگابایت را دانلود کنید.\n"
    "🔓 تهیه اشتراک: /subscription"
)

SUBSCRIPTION_EXPIRY_REMINDER = (
    "اشتراک شما {days} روز دیگر به پایان می‌رسد."
)

PAYMENT_SUCCESS = (
    "پرداخت با موفقیت انجام شد.\n"
    "اشتراک: {tier}\n"
    "انقضا: {expiry}\n"
    "دانلود روزانه: {limit}"
)
PAYMENT_SUCCESS_ADDON = (
    "پرداخت با موفقیت انجام شد.\n"
    "افزونه 2K تا {expiry} فعال است."
)
ADMIN_PAYMENT_NOTICE = "پرداخت موفق: کاربر {user_id} | {provider} | {tier} | {amount}"


PAYMENT_FAILED = "پرداخت ناموفق بود. لطفاً دوباره تلاش کنید."

ADMIN_ONLY = "این دستور فقط برای ادمین‌ها است."

BROADCAST_DONE = "پیام برای {count} کاربر ارسال شد."

GENERIC_ERROR = "خطایی رخ داد. لطفاً بعداً تلاش کنید."

CHOOSE_QUALITY = "کیفیت دلخواه را انتخاب کنید:"
URL_NOT_FOUND = "لینک یافت نشد. دوباره ارسال کنید."
UNLIMITED = "نامحدود"
PAYMENT_COMING_SOON = "پرداخت در نسخه بعدی فعال می‌شود."
RECENT_PAYMENTS = "پرداخت‌های اخیر: {count}"
ENTER_BROADCAST_TEXT = "متن پیام را وارد کنید."
USAGE_BAN = "استفاده: /ban <user_id>"
INVALID_ID = "شناسه نامعتبر."
USER_BANNED = "کاربر مسدود شد."
USAGE_UNBAN = "استفاده: /unban <user_id>"
USER_UNBANNED = "کاربر آزاد شد."
USAGE_GIVE = "استفاده: /give <user_id> <tier> <days>"
INVALID_PARAMS = "پارامترهای نامعتبر."
SUBSCRIPTION_ACTIVATED = "اشتراک فعال شد."
USAGE_REVOKE = "استفاده: /revoke <user_id>"
SUBSCRIPTION_REVOKED = "اشتراک لغو شد."
NO_PAYMENTS = "پرداختی یافت نشد."

PAYMENT_METHOD_PROMPT = "روش پرداخت را انتخاب کنید:"
STARS_INVOICE_SENT = "فاکتور ارسال شد."

# ---------------------------------------------------------------------------
# Card-to-card (manual) payment
# ---------------------------------------------------------------------------

TIER_LABELS = {
    "free": "رایگان",
    "monthly": "اشتراک ماهانه",
    "quarterly": "اشتراک سه‌ماهه",
    "yearly": "اشتراک سالانه",
    "addon_2k": "افزونه 2K",
    "addon": "افزونه 2K",
}


def tier_label(tier: str) -> str:
    """Persian display name for a package/tier key."""
    return TIER_LABELS.get(tier, tier)


CARD_PAYMENT_INSTRUCTIONS = (
    "💳 پرداخت کارت به کارت\n"
    "\n"
    "بسته: {tier_label}\n"
    "مبلغ: {amount} ریال\n"
    "\n"
    "شماره کارت:\n"
    "`{card_number}`\n"
    "به نام: {card_holder}\n"
    "\n"
    "پس از واریز، شماره پیگیری (۴ تا ۶۴ کاراکتر، فقط حروف، رقم و خط تیره) "
    "را همین‌جا ارسال کنید."
)

TRACKING_RECEIVED = (
    "✅ شماره پیگیری شما ثبت شد و در انتظار تأیید مدیر است.\n"
    "پس از تأیید، اشتراک شما فعال می‌شود."
)

TRACKING_INVALID = (
    "❌ شماره پیگیری نامعتبر است.\n"
    "فقط حروف انگلیسی، رقم و خط تیره مجاز است (۴ تا ۶۴ کاراکتر، بدون فاصله).\n"
    "دوباره ارسال کنید."
)

PENDING_EXISTS = (
    "⏳ شما یک پرداخت در انتظار تأیید دارید.\n"
    "تا بررسی آن توسط مدیر صبر کنید."
)

PAYMENT_REJECTED = (
    "❌ پرداخت شما تأیید نشد.\n"
    "در صورت واریز، با پشتیبانی تماس بگیرید."
)

CARD_UPDATED = "✅ اطلاعات کارت به‌روزرسانی شد.\nشماره کارت: `{card_number}`\nبه نام: {card_holder}"
CARD_CURRENT = "💳 اطلاعات کارت فعلی:\nشماره کارت: `{card_number}`\nبه نام: {card_holder}"
CARD_USAGE = "استفاده: /card <شماره کارت> | <نام صاحب کارت>"

NO_PENDING_PAYMENTS = "پرداخت در انتظار تأییدی وجود ندارد."

USAGE_REJECT = "استفاده: /reject <payment_id>"
PAYMENT_NOT_FOUND = "پرداخت یافت نشد."

ADMIN_NEW_PAYMENT = (
    "🆕 پرداخت کارت به کارت جدید\n"
    "\n"
    "کاربر: {full_name} (@{username})\n"
    "شناسه کاربر: {user_id}\n"
    "بسته: {tier_label}\n"
    "مبلغ: {amount} ریال\n"
    "شماره پیگیری: `{tracking_number}`\n"
    "زمان: {created_at}\n"
    "شناسه پرداخت: {payment_id}"
)

ADMIN_PAYMENT_APPROVED = "✅ تأیید شد توسط @{admin_username}"
ADMIN_PAYMENT_REJECTED = "❌ رد شد توسط @{admin_username}"

ADMIN_DUPLICATE_TRACKING_WARNING = (
    "⚠️ شماره پیگیری تکراری!\n"
    "شماره پیگیری `{tracking_number}` قبلاً برای پرداخت #{other_payment_id} "
    "ثبت شده است.\n"
    "این پرداخت به‌صورت خودکار تأیید نشد؛ لطفاً دستی بررسی کنید."
)

ADMIN_APPROVED_USER = "پرداخت #{payment_id} تأیید شد."

ADMIN_APPROVAL_PROMPT = "برای تأیید، بسته مربوط به این پرداخت را انتخاب کنید:"

STAGE_MONTHLY = "اشتراک ماهانه"
STAGE_QUARTERLY = "اشتراک سه‌ماهه"
STAGE_YEARLY = "اشتراک سالانه"
STAGE_ADDON_2K = "افزونه 2K"

STATS_SUMMARY = (
    "کاربران: {users}\n"
    "اشتراک‌دار: {paid}\n"
    "دانلود امروز: {downloads}\n"
    "پرداخت‌های موفق: {payments}\n"
    "پرداخت‌های اخیر: {recent}"
)

BTN_MONTHLY = "ماهانه"
BTN_QUARTERLY = "سه‌ماهه"
BTN_YEARLY = "سالانه"
BTN_ADDON_2K = "افزونه 2K"
BTN_SUBSCRIPTION = "اشتراک"
BTN_HELP = "راهنما"
