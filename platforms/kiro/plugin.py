"""Kiro 平台插件 - 基于 AWS Builder ID 注册"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class KiroPlatform(BasePlatform):
    name = "kiro"
    display_name = "Kiro (AWS Builder ID)"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        from platforms.kiro.core import KiroRegister

        proxy = self.config.proxy
        laoudo_account_id = self.config.extra.get("laoudo_account_id", "")

        reg = KiroRegister(proxy=proxy, tag="KIRO")
        log_fn = getattr(self, '_log_fn', print)
        reg.log = lambda msg: log_fn(msg)

        otp_timeout = int(self.config.extra.get("otp_timeout", 120))

        if self.mailbox:
            mail_acct = self.mailbox.get_email()
            email = email or mail_acct.email
            log_fn(f"邮箱: {mail_acct.email}")
            _before = self.mailbox.get_current_ids(mail_acct)
            def otp_cb():
                log_fn("等待验证码...")
                code = self.mailbox.wait_for_code(mail_acct, keyword="", timeout=otp_timeout, before_ids=_before)
                if code: log_fn(f"验证码: {code}")
                return code
        else:
            otp_cb = None

        ok, info = reg.register(
            email=email,
            pwd=password,
            name=self.config.extra.get("name", "Kiro User"),
            mail_token=laoudo_account_id or None,
            otp_timeout=otp_timeout,
            otp_callback=otp_cb,
        )

        if not ok:
            raise RuntimeError(f"Kiro 注册失败: {info.get('error')}")

        return Account(
            platform="kiro",
            email=info["email"],
            password=info["password"],
            status=AccountStatus.REGISTERED,
            extra={
                "name": info.get("name", ""),
                "accessToken": info.get("accessToken", ""),
                "sessionToken": info.get("sessionToken", ""),
                "clientId": info.get("clientId", ""),
                "clientSecret": info.get("clientSecret", ""),
                "refreshToken": info.get("refreshToken", ""),
            },
        )

    def check_valid(self, account: Account) -> bool:
        """通过 refreshToken 检测账号是否有效"""
        refresh_token = account.extra.get("refreshToken", "")
        if not refresh_token:
            return False
        try:
            from curl_cffi import requests as curl_requests
            r = curl_requests.post(
                "https://oidc.us-east-1.amazonaws.com/token",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": account.extra.get("clientId", ""),
                    "client_secret": account.extra.get("clientSecret", ""),
                },
                impersonate="chrome131",
                timeout=15,
            )
            return r.status_code == 200
        except Exception:
            return False
