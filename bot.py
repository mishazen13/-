import asyncio
import os
import secrets
import string
import time
from dataclasses import dataclass
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import uvicorn


load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_host: str
    api_port: int
    api_key: str
    code_length: int
    code_ttl_seconds: int
    database_path: str


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    api_key = os.getenv("API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_KEY is required")

    return Settings(
        bot_token=bot_token,
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("API_PORT", "8080")),
        api_key=api_key,
        code_length=max(4, int(os.getenv("LINK_CODE_LENGTH", "6"))),
        code_ttl_seconds=max(60, int(os.getenv("LINK_CODE_TTL_SECONDS", "600"))),
        database_path=os.getenv("DATABASE_PATH", "./links.db"),
    )


class LinkConfirmRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    code: str = Field(min_length=4, max_length=16)
    uuid: Optional[str] = None
    ip: Optional[str] = None
    server: Optional[str] = None


class LinkCheckResponse(BaseModel):
    linked: bool
    telegram_id: Optional[int] = None


class LinkService:
    def __init__(self, db_path: str, code_length: int, ttl_seconds: int):
        self.db_path = db_path
        self.code_length = code_length
        self.ttl_seconds = ttl_seconds

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_codes (
                    code TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    nickname TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL,
                    uuid TEXT,
                    linked_at INTEGER NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_links (
                    telegram_id INTEGER PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    linked_at INTEGER NOT NULL
                )
                """
            )
            await db.commit()

    async def cleanup_expired_codes(self) -> None:
        cutoff = int(time.time()) - self.ttl_seconds
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_codes WHERE created_at < ?", (cutoff,))
            await db.commit()

    async def create_code(self, telegram_id: int) -> str:
        await self.cleanup_expired_codes()
        alphabet = string.digits
        for _ in range(10):
            code = "".join(secrets.choice(alphabet) for _ in range(self.code_length))
            now = int(time.time())
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute("DELETE FROM pending_codes WHERE telegram_id = ?", (telegram_id,))
                    await db.execute(
                        "INSERT INTO pending_codes(code, telegram_id, created_at) VALUES (?, ?, ?)",
                        (code, telegram_id, now),
                    )
                    await db.commit()
                return code
            except aiosqlite.IntegrityError:
                continue
        raise RuntimeError("Failed to create unique code")

    async def confirm_link(self, nickname: str, code: str, uuid: Optional[str] = None) -> int:
        await self.cleanup_expired_codes()
        now = int(time.time())

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM pending_codes WHERE code = ?",
                (code,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("invalid_or_expired_code")

            telegram_id = int(row[0])

            await db.execute("DELETE FROM pending_codes WHERE code = ?", (code,))

            await db.execute("DELETE FROM links WHERE nickname = ?", (nickname,))
            await db.execute("DELETE FROM telegram_links WHERE telegram_id = ?", (telegram_id,))

            await db.execute(
                "INSERT INTO links(nickname, telegram_id, uuid, linked_at) VALUES (?, ?, ?, ?)",
                (nickname, telegram_id, uuid, now),
            )
            await db.execute(
                "INSERT INTO telegram_links(telegram_id, nickname, linked_at) VALUES (?, ?, ?)",
                (telegram_id, nickname, now),
            )
            await db.commit()
            return telegram_id

    async def get_link_by_nickname(self, nickname: str) -> Optional[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM links WHERE nickname = ?",
                (nickname,),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def get_link_by_telegram_id(self, telegram_id: int) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT nickname FROM telegram_links WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
        return str(row[0]) if row else None


settings = load_settings()
service = LinkService(
    db_path=settings.database_path,
    code_length=settings.code_length,
    ttl_seconds=settings.code_ttl_seconds,
)
bot = Bot(token=settings.bot_token)
dp = Dispatcher()
app = FastAPI(title="LimboAuth ↔ Telegram Link Bot")


async def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я бот для привязки Minecraft аккаунта.\n"
        "Используй /link чтобы получить одноразовый код для привязки."
    )


@dp.message(Command("link"))
async def cmd_link(message: Message) -> None:
    if message.from_user is None:
        return

    tg_id = message.from_user.id
    linked_nick = await service.get_link_by_telegram_id(tg_id)
    code = await service.create_code(tg_id)

    text = (
        f"Твой код привязки: <code>{code}</code>\n"
        f"Код действует {settings.code_ttl_seconds // 60} мин."
    )
    if linked_nick:
        text += f"\nСейчас к Telegram уже привязан аккаунт: <b>{linked_nick}</b>."

    text += (
        "\n\nДальше введи код на сервере через команду из LimboAuthSocialAddon "
        "(или передай его в ваш bridge-обработчик)."
    )

    await message.answer(text)


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if message.from_user is None:
        return

    nick = await service.get_link_by_telegram_id(message.from_user.id)
    if nick:
        await message.answer(f"Привязано к нику: <b>{nick}</b>")
    else:
        await message.answer("Аккаунт пока не привязан.")


@app.post("/limboauthsocialaddon/confirm", dependencies=[Depends(verify_api_key)])
async def confirm_link(payload: LinkConfirmRequest):
    try:
        telegram_id = await service.confirm_link(
            nickname=payload.nickname,
            code=payload.code,
            uuid=payload.uuid,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    try:
        await bot.send_message(
            telegram_id,
            f"✅ Аккаунт <b>{payload.nickname}</b> успешно привязан к Telegram.",
        )
    except Exception:
        pass

    return {"ok": True, "telegram_id": telegram_id}


@app.get("/limboauthsocialaddon/check/{nickname}", response_model=LinkCheckResponse, dependencies=[Depends(verify_api_key)])
async def check_link(nickname: str):
    tg_id = await service.get_link_by_nickname(nickname)
    return LinkCheckResponse(linked=tg_id is not None, telegram_id=tg_id)


async def run_bot() -> None:
    await dp.start_polling(bot)


async def run_api() -> None:
    config = uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    await service.init()
    await asyncio.gather(run_bot(), run_api())


if __name__ == "__main__":
    asyncio.run(main())
