"""Cursor 平台插件"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register
from platforms.cursor.core import CursorRegister, UA, CURSOR


@register
class CursorPlatform(BasePlatform):
    name = "cursor"
    display_name = "Cursor"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        log = getattr(self, '_log_fn', print)
        proxy = self.config.proxy
        yescaptcha_key = self.config.extra.get("yescaptcha_key", "")

        reg = CursorRegister(proxy=proxy, log_fn=log)

        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()

        def otp_cb():
            log("等待验证码...")
            code = self.mailbox.wait_for_code(mail_acct, keyword="", before_ids=before_ids)
            if code: log(f"验证码: {code}")
            return code

        result = reg.register(
            email=email,
            password=password,
            otp_callback=otp_cb if self.mailbox else None,
            yescaptcha_key=yescaptcha_key,
        )

        return Account(
            platform="cursor",
            email=result["email"],
            password=result["password"],
            token=result["token"],
            status=AccountStatus.REGISTERED,
        )

    def check_valid(self, account: Account) -> bool:
        from curl_cffi import requests as curl_req
        try:
            r = curl_req.get(
                f"{CURSOR}/api/auth/me",
                headers={"Cookie": f"WorkosCursorSessionToken={account.token}",
                         "user-agent": UA},
                impersonate="chrome124", timeout=15,
            )
            return r.status_code == 200
        except Exception:
            return False
