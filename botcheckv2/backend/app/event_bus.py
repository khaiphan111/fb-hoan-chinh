import asyncio
import uuid
import json

class EventBus:
    def __init__(self):
        self.subscribers = {}  # subscriber_id -> asyncio.Queue

    def subscribe(self):
        sub_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        self.subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id):
        if sub_id in self.subscribers:
            del self.subscribers[sub_id]

    async def emit(self, event_type: str, data: dict):
        message = json.dumps({"type": event_type, "data": data})
        for queue in self.subscribers.values():
            await queue.put(message)

event_bus = EventBus()
