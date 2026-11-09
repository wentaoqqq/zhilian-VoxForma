from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from production import conversations

router = APIRouter()


class NewConversation(BaseModel):
    product_id: str
    video_type_id: str
    title: str = Field(default='', max_length=200)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] | None = Field(default=None, max_length=1000)
    request_id: str | None = Field(default=None, max_length=100)


@router.get('/conversations')
def list_conversations(product_id: str | None = None, video_type_id: str | None = None):
    return conversations.list_conversations(product_id, video_type_id)


@router.post('/conversations')
def create_conversation(body: NewConversation):
    try:
        return conversations.create(body.product_id, body.video_type_id, body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: str):
    try:
        return conversations.get(conversation_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/conversations/{conversation_id}/messages')
async def send_message(conversation_id: str, body: Message):
    if not body.text.strip():
        raise HTTPException(400, 'Enter a message.')
    try:
        return await conversations.send(conversation_id, body.text, body.source_ids, body.request_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
