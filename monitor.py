"""Theo dõi hoạt động Đoàn IUH và thông báo qua Telegram khi có thể đăng ký."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


IUH_URL = "https://doantn.iuh.edu.vn/"
STATE_FILE = Path(".monitor-state.json")
ARTIFACT_NAME = "iuh-monitor-state"
GEMINI_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite")


def plain(value: str) -> str:
    """Chuyển chuỗi về dạng không dấu, chữ thường để so sánh."""
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", value.replace("đ", "d")).strip()


@dataclass(frozen=True)
class Activity:
    name: str
    semester: str
    points: str
    deadline: str
    status: str
    details: str
    key: str

    def message(self) -> str:
        lines = [f"🟢 {self.name}"]
        if self.points:
            lines.append(f"Điểm: {self.points}")
        if self.details:
            lines.append(self.details)
        lines.append(IUH_URL)
        return "\n".join(lines)


def parse_activity(raw: dict) -> Activity | None:
    """Trả về Activity nếu còn chỗ dựa theo số lượng (x/y) trong tên."""
    cells = [" ".join(str(x).split()) for x in raw.get("cells", [])]
    if len(cells) != 6:
        return None
    _, name, semester, points, deadline, status = cells
    if not name:
        return None
    details = "\n".join(
        line.strip() for line in str(raw.get("details", "")).splitlines()
        if line.strip()
    )

    # 1. Nếu bản thân đã đăng ký rồi -> Bỏ qua
    if "da dang ky" in plain(status):
        return None

    # 2. Tìm dạng (đã đăng ký / tổng số) trong tên, ví dụ: (150/150)
    match = re.search(r"\((\d+)\s*/\s*(\d+)\)", name)
    if not match:
        return None

    registered, total = int(match.group(1)), int(match.group(2))
    if registered >= total:
        return None  # Đã đủ số lượng (ví dụ: 150/150) -> bỏ qua

    # Còn chỗ trống!
    identity = "|".join((plain(name), plain(semester), plain(details)))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Activity(name, semester, points, deadline, status, details, key)


async def extract_activities(page) -> list[Activity]:
    """Đọc bảng hoạt động và trả về danh sách hoạt động còn đăng ký được."""
    table = page.locator("#tab1 table.tblDoanvien")
    await table.wait_for(state="attached", timeout=20_000)
    rows = await table.locator("tbody > tr").evaluate_all("""rows => rows.map(row => {
        const cells = [...row.querySelectorAll(':scope > td')];

        // Lấy nội dung popup chi tiết qua data-target
        const detailLink = row.querySelector('a[data-target]');
        let details = '';
        if (detailLink) {
          try { details = document.querySelector(detailLink.getAttribute('data-target'))?.innerText || ''; }
          catch { /* Bỏ qua nếu selector lỗi. */ }
        }

        return {cells: cells.map(c => c.innerText.trim()), details};
    })""")

    if not rows:
        print("IUH activity table has no rows.")
        return []
    malformed = sum(len(row.get("cells", [])) != 6 for row in rows)
    if malformed == len(rows):
        if all("khong co" in plain(" ".join(row.get("cells", []))) for row in rows):
            print("IUH activity table reports no activities.")
            return []
        raise RuntimeError("Cau truc bang hoat dong da thay doi.")
    print(f"IUH activity table: {len(rows)} rows, {malformed} malformed.")
    return [activity for row in rows if (activity := parse_activity(row)) is not None]


async def solve_captcha(client: httpx.AsyncClient, image: bytes, api_key: str) -> str:
    """Gửi ảnh CAPTCHA lên Gemini, tự động chuyển model dự phòng nếu gặp lỗi 503."""
    payload = {
        "contents": [{"parts": [
            {"text": "Read the letters and digits in this image. Return only the exact code, with no spaces or explanation."},
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode("ascii")}},
        ]}],
        "generationConfig": {"maxOutputTokens": 256},
    }
    last_error = None
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key}, json=payload,
                )
                response.raise_for_status()
                parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                answer = "".join(part.get("text", "") for part in parts).strip().strip("`'\" ")
                if re.fullmatch(r"[A-Za-z0-9]{1,32}", answer):
                    return answer
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in (500, 502, 503, 504):
                    await asyncio.sleep(2)
                    continue
                break
            except Exception as e:
                last_error = e
                await asyncio.sleep(1)
                continue
    if last_error:
        raise last_error
    raise RuntimeError("Gemini khong tra ve ma CAPTCHA hop le.")


async def read_iuh(client: httpx.AsyncClient, credentials: dict[str, str]) -> list[Activity]:
    """Mở trình duyệt, đăng nhập IUH và trả về danh sách hoạt động còn đăng ký."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            for attempt in range(3):  # Thử lại tối đa 3 lần nếu CAPTCHA sai
                await page.goto(IUH_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.locator("#captcha").wait_for(state="visible", timeout=20_000)
                await page.wait_for_function("""() => {
                    const img = document.querySelector('#captcha');
                    return img && img.complete && img.naturalWidth > 0;
                }""", timeout=20_000)
                image = await page.locator("#captcha").screenshot()
                code = await solve_captcha(client, image, credentials["GEMINI_API_KEY"])
                await page.locator("#mssv").fill(credentials["IUH_MSSV"].strip())
                await page.locator("#password").fill(credentials["IUH_PASSWORD"])
                await page.locator("#security_code").fill(code)
                await page.locator("#frmLogin button[name='btLogin']").click(timeout=20_000)
                try:
                    await page.locator("#tab1 table.tblDoanvien").wait_for(state="attached", timeout=15_000)
                except PlaywrightTimeoutError:
                    print(f"IUH login attempt {attempt + 1}/3 did not reach the activity table.")
                    continue
                return await extract_activities(page)
            raise RuntimeError("Khong dang nhap duoc sau 3 CAPTCHA; kiem tra tai khoan hoac giao dien IUH.")
        finally:
            await browser.close()


async def load_seen(client: httpx.AsyncClient, token: str, repository: str, run_id: str) -> set[str]:
    """Tải danh sách hash hoạt động đã thông báo từ GitHub Artifact."""
    api = f"https://api.github.com/repos/{repository}/actions/artifacts"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    response = await client.get(api, params={"name": ARTIFACT_NAME, "per_page": 100}, headers=headers)
    response.raise_for_status()
    candidates = [item for item in response.json().get("artifacts", [])
                  if not item.get("expired") and str(item.get("workflow_run", {}).get("id")) != run_id]
    if not candidates:
        return set()
    latest = max(candidates, key=lambda item: item["created_at"])
    archive = await client.get(f"{api}/{latest['id']}/zip", headers=headers)
    archive.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        data = json.loads(bundle.read(STATE_FILE.name))
    if data.get("version") != 1 or not isinstance(data.get("seen_open_ids"), list):
        raise RuntimeError("Artifact trang thai khong hop le.")
    return set(data["seen_open_ids"])


async def send_telegram(client: httpx.AsyncClient, token: str, chat_id: str, message: str) -> None:
    """Gửi tin nhắn lên Telegram bot."""
    response = await client.post(
        f"https://api.telegram.org/bot{quote(token, safe='')}/sendMessage",
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram khong chap nhan tin nhan.")


async def main() -> int:
    required = ("IUH_MSSV", "IUH_PASSWORD", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
        return 1
    config = {name: os.environ[name] for name in required}
    stage = "load notification history"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            seen = await load_seen(client, config["GITHUB_TOKEN"], config["GITHUB_REPOSITORY"], config["GITHUB_RUN_ID"])
            stage = "log in and read IUH"
            activities = await read_iuh(client, config)
            fresh = [activity for activity in activities if activity.key not in seen]
            stage = "send Telegram"
            if os.getenv("MANUAL_RUN") == "true":
                await send_telegram(client, config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"],
                                    f"IUH: kiểm tra thành công. Có {len(activities)} hoạt động đang cho đăng ký; {len(fresh)} hoạt động mới có chỗ.")
            for activity in fresh:
                await send_telegram(client, config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], activity.message())
                await asyncio.sleep(1)
            seen.update(activity.key for activity in activities)
            stage = "write notification history"
            STATE_FILE.write_text(json.dumps({"version": 1, "seen_open_ids": sorted(seen)}, ensure_ascii=False), encoding="utf-8")
            print(f"Check complete: {len(activities)} open activities, {len(fresh)} notifications.")
            return 0
        except Exception as exc:
            # Không in nội dung lỗi vì HTTP error có thể chứa URL mang token/mật khẩu
            status = f" (HTTP {exc.response.status_code})" if isinstance(exc, httpx.HTTPStatusError) else ""
            print(f"Check failed at {stage}: {type(exc).__name__}{status}. See README troubleshooting.", file=sys.stderr)
            if os.getenv("MANUAL_RUN") == "true":
                try:
                    await send_telegram(client, config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"],
                                        "IUH: lượt kiểm tra thất bại. Xem log GitHub Actions; kiểm tra secrets, CAPTCHA và giao diện trang.")
                except Exception:
                    pass
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
