import asyncio
from logging import basicConfig, DEBUG, INFO # pylint:disable=unused-import
import os
from signal import SIGINT, SIGTERM

from core.application import Application

basicConfig(level=DEBUG)

loop = asyncio.get_event_loop()
try:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app = Application(loop, base_dir)
    try:
        loop.add_signal_handler(SIGINT, app.shutdown)
        loop.add_signal_handler(SIGTERM, app.shutdown)

        loop.run_until_complete(app.startup())
    finally:
        loop.run_until_complete(app.close())

    # wait all tasks
    loop.run_until_complete(asyncio.gather(*asyncio.Task.all_tasks()))
finally:
    loop.close()
