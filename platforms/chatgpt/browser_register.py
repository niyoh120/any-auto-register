"""ChatGPT 浏览器注册流程（Camoufox）。"""
import base64
import json
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from camoufox.sync_api import Camoufox

OPENAI_AUTH = "https://auth.openai.com"
CHATGPT_APP = "https://chatgpt.com"


def _build_proxy_config(proxy: Optional[str]) -> Optional[dict]:
    if not proxy:
        return None
    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        return {"server": proxy}
    config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return config


def _wait_for_url(page, substring: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if substring in page.url:
            return True
        time.sleep(1)
    return False


def _wait_for_any_selector(page, selectors: list[str], timeout: int = 30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                node = page.query_selector(sel)
            except Exception:
                node = None
            if node:
                return sel
        time.sleep(0.5)
    return None


def _click_first(page, selectors: list[str], *, timeout: int = 10) -> str | None:
    found = _wait_for_any_selector(page, selectors, timeout=timeout)
    if not found:
        return None
    try:
        page.click(found)
        return found
    except Exception:
        return None


def _dump_debug(page, prefix: str) -> None:
    page.screenshot(path=f"/tmp/{prefix}.png")
    with open(f"/tmp/{prefix}.html", "w") as f:
        f.write(page.content())


def _get_cookies(page) -> dict:
    return {c["name"]: c["value"] for c in page.context.cookies()}


def _do_codex_oauth(cookies_dict: dict, proxy: str | None, log) -> dict | None:
    """用 chatgpt.com session cookies 通过协议完成 Codex CLI OAuth。
    返回 {access_token, refresh_token, id_token, account_id} 或 None。"""
    from .oauth import generate_oauth_url, submit_callback_url
    from curl_cffi import requests as cffi_requests

    oauth_start = generate_oauth_url()
    log(f"  OAuth state={oauth_start.state[:20]}...")

    s = cffi_requests.Session(impersonate="chrome131")
    if proxy:
        from urllib.parse import urlparse as _up
        s.proxies = {"http": proxy, "https": proxy}

    # 把浏览器 cookies 设到 session 上
    for name, value in cookies_dict.items():
        # auth.openai.com 和 chatgpt.com 都设
        for domain in [".openai.com", ".chatgpt.com", ".auth.openai.com"]:
            s.cookies.set(name, value, domain=domain, path="/")

    try:
        # 访问 authorize URL，跟随重定向直到拿到 callback
        r = s.get(oauth_start.auth_url, headers={
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "accept": "text/html",
        }, allow_redirects=True, timeout=30)

        final_url = str(r.url)
        log(f"  OAuth redirect → {final_url[:100]}...")

        if "localhost" in final_url and "code=" in final_url:
            result_json = submit_callback_url(
                callback_url=final_url,
                expected_state=oauth_start.state,
                code_verifier=oauth_start.code_verifier,
            )
            return json.loads(result_json)

        # 可能停在 auth.openai.com 登录页，需要用 session_token 登录
        # 尝试 POST 到 authorize endpoint 带 session cookie
        log(f"  OAuth 未自动跳转，尝试带 session 重新请求...")

        # 从 cookies 里找 session token
        session_token = cookies_dict.get("__Secure-next-auth.session-token", "")
        if not session_token:
            log("  ⚠️ 无 session_token，OAuth 失败")
            return None

        # 用 session_token 刷新拿 access_token，然后用 access_token 做 OAuth
        s2 = cffi_requests.Session(impersonate="chrome131")
        if proxy:
            s2.proxies = {"http": proxy, "https": proxy}
        s2.cookies.set("__Secure-next-auth.session-token", session_token,
                       domain=".chatgpt.com", path="/")
        r2 = s2.get("https://chatgpt.com/api/auth/session",
                     headers={"accept": "application/json"}, timeout=15)
        if r2.status_code == 200:
            data = r2.json()
            at = data.get("accessToken", "")
            if at:
                # 用这个 access_token 做 OAuth refresh_token 交换
                from .constants import OAUTH_CLIENT_ID, OAUTH_TOKEN_URL
                # 尝试用 session 的 access_token 换 refresh_token
                # 这不是标准 OAuth，但有些实现支持
                log(f"  session access_token 获取成功，解析 account_id")
                payload = {}
                try:
                    parts = at.split(".")
                    if len(parts) >= 2:
                        import base64
                        pb = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(pb))
                except Exception:
                    pass
                auth_info = payload.get("https://api.openai.com/auth", {})
                account_id = auth_info.get("chatgpt_account_id", "")
                exp = payload.get("exp", 0)
                import time as _time
                expired = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(exp)) if exp else ""
                now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
                return {
                    "access_token": at,
                    "refresh_token": "",
                    "id_token": "",
                    "account_id": account_id,
                    "email": payload.get("email", ""),
                    "expired": expired,
                    "last_refresh": now,
                    "type": "codex",
                }
        log("  ⚠️ session 刷新也失败")
        return None
    except Exception as e:
        log(f"  OAuth 异常: {e}")
        return None


def _wait_for_access_token(page, timeout: int = 60) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = page.evaluate("""
            async () => {
                const r = await fetch('/api/auth/session');
                const j = await r.json();
                return j.accessToken || '';
            }
            """)
            if r:
                return r
        except Exception:
            pass
        time.sleep(2)
    return ""


class ChatGPTBrowserRegister:
    def __init__(
        self,
        *,
        headless: bool,
        proxy: Optional[str] = None,
        otp_callback: Optional[Callable[[], str]] = None,
        log_fn: Callable[[str], None] = print,
    ):
        self.headless = headless
        self.proxy = proxy
        self.otp_callback = otp_callback
        self.log = log_fn

    def run(self, email: str, password: str) -> dict:
        entry_selectors = [
            'a[href*="sign-up"]',
            'a[href*="create-account"]',
            'button:has-text("Sign up")',
            'a:has-text("Sign up")',
            'button:has-text("Sign up for free")',
            'a:has-text("Sign up for free")',
        ]
        email_selectors = [
            'input[name="email"]',
            'input[type="email"]',
            'input[autocomplete="username"]',
            'input#email',
        ]
        continue_selectors = [
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Continue with email")',
            'button:has-text("Next")',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            'input[autocomplete="new-password"]',
            'input[autocomplete="current-password"]',
        ]
        otp_selectors = [
            'input[name="code"]',
            'input[autocomplete="one-time-code"]',
            'input[inputmode="numeric"]',
            'input[maxlength="1"]',
            'input[placeholder*="code"]',
            'input[placeholder*="Code"]',
        ]

        proxy = _build_proxy_config(self.proxy)
        launch_opts = {"headless": self.headless}
        if proxy:
            launch_opts["proxy"] = proxy

        with Camoufox(**launch_opts) as browser:
            page = browser.new_page()
            self.log("打开 ChatGPT 注册页")
            page.goto(f"{CHATGPT_APP}/auth/login", wait_until="networkidle", timeout=30000)
            time.sleep(2)
            self.log(f"当前页面: {page.url}")

            if "auth.openai.com" not in page.url:
                clicked = _click_first(page, entry_selectors, timeout=8)
                if clicked:
                    self.log(f"已点击注册入口: {clicked}")
                    time.sleep(3)
                else:
                    self.log("未在入口页找到 Sign up，继续等待统一认证页跳转")

            if "auth.openai.com" not in page.url and not _wait_for_url(page, "auth.openai.com", timeout=20):
                self.log(f"未检测到 auth.openai.com 跳转，继续尝试当前页表单: {page.url}")
            else:
                self.log(f"已进入统一认证页: {page.url}")

            # Fill email
            email_sel = _wait_for_any_selector(page, email_selectors, timeout=25)
            if not email_sel:
                self.log("未找到邮箱输入框，保存调试文件到 /tmp/chatgpt_email_fail.*")
                _dump_debug(page, "chatgpt_email_fail")
                raise RuntimeError(f"未找到邮箱输入框: {page.url}")
            self.log(f"已定位邮箱输入框: {email_sel}")
            page.fill(email_sel, email)

            used_continue = _click_first(page, continue_selectors, timeout=5)
            if used_continue:
                self.log(f"已点击邮箱页继续按钮: {used_continue}")
            time.sleep(3)

            # Password step
            pwd_sel = _wait_for_any_selector(page, password_selectors, timeout=20)
            if pwd_sel:
                self.log(f"已定位密码输入框: {pwd_sel}")
                page.fill(pwd_sel, password)
                used_continue = _click_first(page, continue_selectors, timeout=5)
                if used_continue:
                    self.log(f"已点击密码页继续按钮: {used_continue}")
                time.sleep(3)

            # OTP step
            otp_sel = _wait_for_any_selector(page, otp_selectors, timeout=25)
            if not otp_sel:
                self.log("未进入验证码页面，保存调试文件到 /tmp/chatgpt_otp_fail.*")
                _dump_debug(page, "chatgpt_otp_fail")
                raise RuntimeError(f"未进入验证码页面: {page.url}")

            if not self.otp_callback:
                raise RuntimeError("ChatGPT 注册需要邮箱验证码但未提供 otp_callback")
            self.log("等待 ChatGPT 验证码")
            code = self.otp_callback()
            if not code:
                raise RuntimeError("未获取到验证码")
            self.log(f"已定位验证码输入框: {otp_sel}")
            if otp_sel == 'input[maxlength="1"]':
                try:
                    page.click(otp_sel)
                except Exception:
                    pass
                for digit in str(code).strip():
                    page.keyboard.press(digit)
                    time.sleep(0.1)
            else:
                page.fill(otp_sel, code)
            used_continue = _click_first(page, continue_selectors, timeout=5)
            if used_continue:
                self.log(f"已点击验证码页继续按钮: {used_continue}")
            time.sleep(5)

            # Check for about-you page ("How old are you?")
            self.log("等待可能的 Name/Age 填写步骤...")
            for _ in range(15):
                if "chatgpt.com" in page.url:
                    break
                # 新版: Full name + Age 输入框
                name_sel = page.query_selector('input[name="name"]')
                if name_sel:
                    self.log("检测到关于您页面，填写姓名和年龄")
                    import random, string
                    first = ''.join(random.choices(string.ascii_lowercase, k=6)).capitalize()
                    last = ''.join(random.choices(string.ascii_lowercase, k=6)).capitalize()
                    # 如果 name 已经有值就不覆盖
                    cur_name = name_sel.get_attribute("value") or ""
                    if not cur_name.strip():
                        page.fill('input[name="name"]', f"{first} {last}")

                    # 新版 Age 输入框
                    age_sel = page.query_selector('input[name="age"]')
                    if age_sel:
                        self.log("检测到 Age 输入框 (新版)")
                        page.fill('input[name="age"]', str(random.randint(25, 35)))
                    # 旧版 birthday (React Aria DateField) 兼容
                    elif page.query_selector('div[data-type="month"]'):
                        self.log("检测到 Birthday 输入框 (旧版)")
                        page.click('div[data-type="month"]', force=True)
                        page.keyboard.type("01")
                        time.sleep(0.5)
                        page.click('div[data-type="day"]', force=True)
                        page.keyboard.type("01")
                        time.sleep(0.5)
                        page.click('div[data-type="year"]', force=True)
                        page.keyboard.type("1990")

                    time.sleep(1)
                    submit_btn = ('button[type="submit"],'
                                  ' button:has-text("Finish"),'
                                  ' button:has-text("Finish creating account")')
                    if page.query_selector(submit_btn):
                        page.click(submit_btn)
                    time.sleep(5)
                    break
                time.sleep(1)

            # Wait for chatgpt.com
            try:
                page.wait_for_url("**/chatgpt.com**", timeout=45000)
            except Exception:
                if not _wait_for_url(page, "chatgpt.com", timeout=15):
                    self.log("未跳转到应用，保存截图到 /tmp/chatgpt_fail.png")
                    _dump_debug(page, "chatgpt_fail")
                    raise RuntimeError(f"ChatGPT 注册后未跳转到应用: {page.url}")

            time.sleep(3)
            # 处理浏览器 persistent storage 弹窗 (Allow/Block)
            try:
                allow_btn = page.query_selector('button:has-text("Allow")')
                if allow_btn:
                    self.log("点击 Allow (persistent storage)")
                    allow_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # 处理 onboarding 流程 — 可能有多步
            self.log("处理 onboarding 引导页...")
            for attempt in range(8):
                time.sleep(2)
                # "Okay, let's go" 按钮 (Tips 弹窗)
                okay_btn = page.query_selector('button:has-text("Okay, let\'s go")')
                if okay_btn:
                    self.log(f"点击 Okay, let's go (第{attempt+1}次)")
                    try:
                        okay_btn.click()
                    except Exception:
                        pass
                    time.sleep(2)
                    continue
                # Skip 按钮
                skip_btn = page.query_selector('button:has-text("Skip")')
                if skip_btn:
                    self.log(f"点击 Skip (第{attempt+1}次)")
                    try:
                        skip_btn.click()
                    except Exception:
                        pass
                    time.sleep(2)
                    continue
                # Continue 按钮 ("You're all set" 页面)
                continue_btn = page.query_selector('button:has-text("Continue")')
                if continue_btn:
                    self.log(f"点击 Continue (第{attempt+1}次)")
                    try:
                        continue_btn.click()
                    except Exception:
                        pass
                    time.sleep(2)
                    continue
                # Next 按钮
                next_btn = page.query_selector('button:has-text("Next")')
                if next_btn:
                    self.log(f"点击 Next (第{attempt+1}次)")
                    try:
                        next_btn.click()
                    except Exception:
                        pass
                    time.sleep(2)
                    continue
                # 检查是否已到主界面 (有输入框)
                if page.query_selector('textarea, div[contenteditable="true"]'):
                    self.log("已进入主界面")
                    break
                # 没有更多引导按钮了
                break
            time.sleep(3)

            # 获取 session token 和 cookies
            cookies_dict = _get_cookies(page)
            session_token = cookies_dict.get("__Secure-next-auth.session-token", "")
            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

            # ═══ 通过 Codex CLI OAuth 获取正确的 token ═══
            # 用 session cookies 在协议层完成 OAuth（不需要浏览器交互）
            self.log("执行 Codex CLI OAuth 流程获取 token...")
            codex_result = _do_codex_oauth(cookies_dict, self.proxy, self.log)

            if codex_result:
                self.log(f"Codex OAuth 成功: account_id={codex_result.get('account_id','')}")
                self.log(f"注册成功: {email}")
                return {
                    "email": email, "password": password,
                    "account_id": codex_result.get("account_id", ""),
                    "access_token": codex_result.get("access_token", ""),
                    "refresh_token": codex_result.get("refresh_token", ""),
                    "id_token": codex_result.get("id_token", ""),
                    "session_token": session_token,
                    "workspace_id": "", "cookies": cookie_str,
                    "profile": {},
                }

            # fallback: OAuth 失败，用 session access_token
            self.log("Codex OAuth 失败，回退到 session token")
            access_token = _wait_for_access_token(page, timeout=15)
            account_id = ""
            if access_token:
                try:
                    parts = access_token.split(".")
                    if len(parts) >= 2:
                        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                        auth_info = payload.get("https://api.openai.com/auth", {})
                        account_id = auth_info.get("chatgpt_account_id", "")
                except Exception:
                    pass
            self.log(f"注册成功 (session token 模式): {email}")
            return {
                "email": email, "password": password,
                "account_id": account_id,
                "access_token": access_token,
                "refresh_token": "", "id_token": "",
                "session_token": session_token,
                "workspace_id": "", "cookies": cookie_str,
                "profile": {},
            }
