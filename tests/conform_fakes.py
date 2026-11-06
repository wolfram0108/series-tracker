"""A scripted alignment daemon for tests of the conform bridge and of the modules that use it."""
import asyncio

import httpx

from core.reasons import reason
from modules.conform import DaemonRefused, Unconfirmed


class FakeClient:
    """A daemon that plays scripted connections: a list of events, or an exception to raise."""

    base_url = "http://daemon.test"

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.connections = 0
        self.enqueued = []
        self.reversed = []
        self.cancelled = []
        self.refuse = False
        self.lose_answers = 0
        self.known = {}
        self.plots = {}  # (job id, name) -> the picture the daemon holds
        self.asked = []  # the plots that were asked for
        self.drained = asyncio.Event()  # set when the bridge has taken in every event of a script and asks for more

    async def info(self):
        return {"version": "0.1.0", "device": "cuda", "gpu": True, "gpu_name": "RTX"}

    async def events(self):
        self.connections += 1
        if not self.scripts:
            await asyncio.Event().wait()
        for item in self.scripts.pop(0):
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)
            yield item
        self.drained.set()  # the consumer comes back for the next event only after it has finished with the last one
        await asyncio.Event().wait()

    async def enqueue(self, body):
        self.enqueued.append(body)
        if isinstance(self.refuse, Exception):
            raise self.refuse
        if self.refuse:
            raise DaemonRefused(reason("daemon_refused", status=422, text="bad path"))
        if self.lose_answers:  # the daemon takes the job and the answer never comes back
            self.lose_answers -= 1
            job_id = f"j-{len(self.enqueued)}"
            self.known[job_id] = {"id": job_id, "label": body.get("label"), "status": "queued"}
            raise Unconfirmed("ReadTimeout: no answer")
        return {"id": "j-new", "status": "queued"}

    async def reverse(self, body):
        """A batch of cuts: one job per episode, labelled by the folder of the episode, as the daemon does."""
        self.reversed.append(body)
        if isinstance(self.refuse, Exception):
            raise self.refuse
        if self.refuse:
            raise DaemonRefused(reason("daemon_refused", status=422, text="bad path"))
        jobs = []
        for path in body["episodes"]:
            job_id = f"r-{len(self.known) + 1}"
            self.known[job_id] = {"id": job_id, "kind": "reverse", "label": path.rsplit("/", 2)[-2], "status": "queued"}
            jobs.append(self.known[job_id])
        if self.lose_answers:
            self.lose_answers -= 1
            raise Unconfirmed("ReadTimeout: no answer")
        return jobs

    async def job(self, job_id):
        return self.known.get(job_id)

    async def jobs(self):
        if isinstance(self.refuse, Exception):
            raise self.refuse
        return list(self.known.values())

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        if isinstance(self.refuse, Exception):
            raise self.refuse
        return job_id in self.known

    async def plot(self, job_id, name):
        self.asked.append((job_id, name))
        if isinstance(self.refuse, Exception):
            raise self.refuse
        return self.plots.get((job_id, name))

    async def close(self):
        pass
