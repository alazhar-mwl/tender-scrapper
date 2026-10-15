import uvicorn

from . import config
from .db import upgrade_db

if __name__ == "__main__":
    upgrade_db()
    uvicorn.run("tenderiq.main:app", host=config.API_HOST, port=config.API_PORT)
