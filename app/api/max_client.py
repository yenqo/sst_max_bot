import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class MaxApiClient:
    def __init__(self):
        self.base_url = settings.max_api_base_url.rstrip('/')
        self.token = settings.max_bot_token
        self.headers = {
            "Authorization": self.token,
            "Content-Type": "application/json"
        }
        
    async def get_me(self) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/me", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def send_message(self, chat_id: str, text: str, keyboard: list = None) -> dict:
        """
        Sends a message to the user.
        Keyboard format assumed to be standard inline-like.
        """
        payload = {
            "chat_id": chat_id,
            "text": text
        }
        if keyboard:
            payload["keyboard"] = keyboard

        async with httpx.AsyncClient() as client:
            try:
                # We assume a generic /messages/send endpoint
                resp = await client.post(f"{self.base_url}/messages/send", headers=self.headers, json=payload)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to send message to {chat_id}: {e}")
                return {}

    async def get_updates(self, offset: int = 0) -> list:
        """
        For polling mode.
        """
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{self.base_url}/updates?offset={offset}", headers=self.headers)
                resp.raise_for_status()
                return resp.json().get("updates", [])
            except httpx.HTTPError as e:
                logger.error(f"Failed to get updates: {e}")
                return []
                
max_client = MaxApiClient()
