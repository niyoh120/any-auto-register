"""Trae.ai 平台插件"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class TraePlatform(BasePlatform):
    name = "trae"
    display_name = "Trae.ai"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        from platforms.trae.core import TraeRegister
        log = getattr(self, '_log_fn', print)

        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        log(f"邮箱: {email}")
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()

        def otp_cb():
            log("等待验证码...")
            code = self.mailbox.wait_for_code(mail_acct, keyword="", before_ids=before_ids)
            if code: log(f"验证码: {code}")
            return code

        with self._make_executor() as ex:
            reg = TraeRegister(executor=ex, log_fn=log)
            result = reg.register(
                email=email,
                password=password,
                otp_callback=otp_cb if self.mailbox else None,
            )

        return Account(
            platform="trae",
            email=result["email"],
            password=result["password"],
            user_id=result["user_id"],
            token=result["token"],
            region=result["region"],
            status=AccountStatus.REGISTERED,
            extra={"cashier_url": result["cashier_url"],
                   "ai_pay_host": result["ai_pay_host"]},
        )

    def check_valid(self, account: Account) -> bool:
        return bool(account.token)
