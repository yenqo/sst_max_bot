import ssl
from pathlib import Path

import certifi
import httpx
from app.core.config import settings
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# Long polling timeout on the MAX side (seconds, 0-90)
POLL_TIMEOUT = 30


# platform-api2.max.ru is signed by the Russian Trusted Root CA (Ministry of Digital
# Development), which is not in the default certifi bundle. Source:
# https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer
RUSSIAN_ROOT_CA = Path(__file__).resolve().parent.parent / "certs" / "russian_trusted_root_ca.pem"


def build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(RUSSIAN_ROOT_CA))
    return ctx


def build_keyboard(rows: List[List[dict]]) -> dict:
    """
    Converts a compact keyboard [[{"text": ..., "callback_data": ...}]]
    into a MAX inline_keyboard attachment. Buttons with "url" instead of
    "callback_data" become link buttons.
    """
    def convert(btn: dict) -> dict:
        if "url" in btn:
            return {"type": "link", "text": btn["text"], "url": btn["url"]}
        return {"type": "callback", "text": btn["text"], "payload": btn["callback_data"]}

    buttons = [[convert(btn) for btn in row] for row in rows]
    return {"type": "inline_keyboard", "payload": {"buttons": buttons}}


def location_attachment(latitude: float, longitude: float) -> dict:
    return {"type": "location", "latitude": latitude, "longitude": longitude}


class MaxApiClient:
    def __init__(self):
        self.base_url = settings.max_api_base_url.rstrip('/')
        self.token = settings.max_bot_token
        self.headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        # Read timeout must exceed POLL_TIMEOUT, otherwise every long poll fails
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            verify=build_ssl_context(),
            timeout=httpx.Timeout(10.0, read=POLL_TIMEOUT + 15),
        )

    async def close(self):
        await self.client.aclose()

    async def get_me(self) -> dict:
        resp = await self.client.get("/me")
        resp.raise_for_status()
        return resp.json()

    async def _post_message(self, user_id: str, payload: dict) -> dict:
        resp = await self.client.post("/messages", params={"user_id": user_id}, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, user_id: str, text: str, keyboard: list = None) -> dict:
        """
        Sends a markdown message to the user's dialog with the bot.
        keyboard: [[{"text": ..., "callback_data": ...}]]
        """
        payload = {"text": text, "format": "markdown"}
        if keyboard:
            payload["attachments"] = [build_keyboard(keyboard)]

        try:
            return await self._post_message(user_id, payload)
        except httpx.HTTPError as e:
            body = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else ""
            logger.error(f"Failed to send message to {user_id}: {e} {body}")
            return {}

    async def send_location(self, user_id: str, latitude: float, longitude: float, text: str = None) -> dict:
        """
        Sends a native MAX location (map pin) to the user's dialog.
        Note: MAX shows only the map for such a message, the text is not displayed.
        """
        payload = {"attachments": [location_attachment(latitude, longitude)]}
        if text:
            payload.update(text=text, format="markdown")
        try:
            return await self._post_message(user_id, payload)
        except httpx.HTTPError as e:
            body = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else ""
            logger.error(f"Failed to send location to {user_id}: {e} {body}")
            return {}

    async def edit_by_callback(self, callback_id: str, text: str, keyboard: list = None) -> bool:
        """
        Answers a button press by replacing the message the button belongs to.
        Returns False if MAX did not accept the answer (e.g. the callback expired).
        """
        message = {"text": text, "format": "markdown"}
        if keyboard:
            message["attachments"] = [build_keyboard(keyboard)]
        try:
            resp = await self.client.post("/answers", params={"callback_id": callback_id}, json={"message": message})
            resp.raise_for_status()
            return resp.json().get("success", True)
        except httpx.HTTPError as e:
            details = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else ""
            logger.error(f"Failed to edit message by callback {callback_id}: {e} {details}")
            return False

    async def get_updates(self, marker: Optional[int] = None) -> Tuple[list, Optional[int]]:
        """
        For polling mode. Returns (updates, next_marker).
        """
        params = {
            "timeout": POLL_TIMEOUT,
            "types": "message_created,message_callback,bot_started",
        }
        if marker is not None:
            params["marker"] = marker
        try:
            resp = await self.client.get("/updates", params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("updates", []), data.get("marker", marker)
        except httpx.HTTPError as e:
            logger.error(f"Failed to get updates: {e}")
            return [], marker

max_client = MaxApiClient()
