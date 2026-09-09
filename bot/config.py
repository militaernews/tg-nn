import os
from datetime import datetime
from typing import Final

from dotenv import load_dotenv

load_dotenv()

CHANNEL_TEST = -1001391125365
CHANNEL_BACKUP = -1001861018052
GROUP_SOURCE = 1723195485  # requires pattern topic
GROUP_PATTERN = -1001895734902
GROUP_LOG = -1001723195485

# Shared error-log group/topic used by all ptb-* bots (ptb-mnchat etc.) -
# distinct from GROUP_LOG above, which is tg-nn's own group for other logging.
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", -1001338514957))
THREAD_ID = int(os.getenv("THREAD_ID", 488))  # tg-nn topic

CHANNEL_UA = -1001839268196

DEEPL = os.getenv("DEEPL")
DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL_TEST: Final[str] = os.getenv("DATABASE_URL_TEST")
PASSWORD = os.getenv("PASSWORD")
OPENROUTER_API_KEY=os.getenv("OPENROUTER_API_KEY")
GIVEAWAY_DESTINATION = os.getenv("GIVEAWAY_DESTINATION")

TESTING = False
LOG_FILENAME = rf"./logs/{datetime.now().strftime('%Y-%m-%d/%H-%M-%S')}.log"

CONTAINER: Final[bool] = bool(os.getenv('CONTAINER', False), )

RES_PATH: Final[str] = "./res"
