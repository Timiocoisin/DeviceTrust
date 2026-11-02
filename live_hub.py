# -*- coding: utf-8 -*-
"""进程内实时推送总线：授权服务写入，WebSocket 订阅端读出。"""
from __future__ import annotations

import json
import threading
from queue import Empty, Full, Queue


class LiveHub:
    def __init__(self, maxsize: int = 200):
        self._lock = threading.Lock()
        self._subs: list[Queue] = []
        self._maxsize = maxsize

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        payload = dict(event)
        with self._lock:
            subs = list(self._subs)
        dead = []
        for q in subs:
            try:
                q.put_nowait(payload)
            except Full:
                # 丢弃最旧再塞，避免慢客户端拖垮总线
                try:
                    q.get_nowait()
                except Empty:
                    pass
                try:
                    q.put_nowait(payload)
                except Full:
                    dead.append(q)
        if dead:
            with self._lock:
                for q in dead:
                    if q in self._subs:
                        self._subs.remove(q)

    @staticmethod
    def dumps(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False, default=str)


live_hub = LiveHub()
