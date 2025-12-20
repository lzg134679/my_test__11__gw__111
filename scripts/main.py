import glob
import logging
import logging.config
import os
import shutil
import sys
import time
import schedule
import json
import random
from datetime import datetime, timedelta


def apply_local_overrides():
    """Copy local debug scripts from /config/gwkz/scripts into the container."""
    override_dir = "/config/gwkz/scripts"
    target_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(override_dir):
        print(f"正常运行在非 Home Assistant 环境，跳过本地脚本覆盖步骤。")
        return
    override_files = [p for p in glob.glob(os.path.join(override_dir, "*.py")) if os.path.isfile(p)]
    if not override_files:
        print(f"在 {override_dir} 中未找到任何 .py 文件，跳过本地脚本覆盖步骤。")
        return
    print(f"[覆盖运行] 在HA目录“{override_dir}”中找到{len(override_files)}个py文件，正在复制到容器中...")
    for src in override_files:
        dest = os.path.join(target_dir, os.path.basename(src))
        try:
            shutil.copy2(src, dest)
            print(f"[覆盖运行] 已将“{src}”中的py文件替换进“{dest}”")
        except Exception as exc:
            print(f"[覆盖运行] 复制“{src}”失败: {exc}")

apply_local_overrides()

from error_watcher import ErrorWatcher
from const import *
from data_fetcher import DataFetcher

def main():
    global RETRY_TIMES_LIMIT
    if 'PYTHON_IN_DOCKER' not in os.environ: 
        # 读取 .env 文件
        import dotenv
        dotenv.load_dotenv(verbose=True)
    if os.path.isfile('/data/options.json'):
        with open('/data/options.json') as f:
            options = json.load(f)
        try:
            PHONE_NUMBER = options.get("PHONE_NUMBER")
            PASSWORD = options.get("PASSWORD")
            HASS_URL = options.get("HASS_URL")
            JOB_START_TIME = options.get("JOB_START_TIME", "07:00")
            LOG_LEVEL = options.get("LOG_LEVEL", "INFO")
            VERSION = os.getenv("VERSION")
            RETRY_TIMES_LIMIT = int(options.get("RETRY_TIMES_LIMIT", 5))

            logger_init(LOG_LEVEL)
            os.environ["HASS_URL"] = options.get("HASS_URL", "http://homeassistant.local:8123/")
            os.environ["HASS_TOKEN"] = options.get("HASS_TOKEN", "")
            os.environ["ENABLE_DATABASE_STORAGE"] = str(options.get("ENABLE_DATABASE_STORAGE", "false")).lower()
            os.environ["IGNORE_USER_ID"] = options.get("IGNORE_USER_ID", "xxxxx,xxxxx")
            os.environ["DB_NAME"] = options.get("DB_NAME", "homeassistant.db")
            os.environ["RETRY_TIMES_LIMIT"] = str(options.get("RETRY_TIMES_LIMIT", 5))
            os.environ["DRIVER_IMPLICITY_WAIT_TIME"] = str(options.get("DRIVER_IMPLICITY_WAIT_TIME", 60))
            os.environ["LOGIN_EXPECTED_TIME"] = str(options.get("LOGIN_EXPECTED_TIME", 10))
            os.environ["RETRY_WAIT_TIME_OFFSET_UNIT"] = str(options.get("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
            os.environ["DATA_RETENTION_DAYS"] = str(options.get("DATA_RETENTION_DAYS", 7))
            os.environ["RECHARGE_NOTIFY"] = str(options.get("RECHARGE_NOTIFY", "false")).lower()
            os.environ["BALANCE"] = str(options.get("BALANCE", 5.0))
            os.environ["PUSHPLUS_TOKEN"] = options.get("PUSHPLUS_TOKEN", "")
            os.environ["RUN_AT_START"] = str(options.get("RUN_AT_START", "true")).lower()
            RUN_AT_START = str(options.get("RUN_AT_START", "true")).lower() == "true"
            logging.info(f"当前以 Homeassistant 插件形式运行。")
        except Exception as e:
            logging.error(f"读取 options.json 失败，程序将退出，原因: {e}。")
            sys.exit()
    else:
        try:
            PHONE_NUMBER = os.getenv("PHONE_NUMBER")
            PASSWORD = os.getenv("PASSWORD")
            HASS_URL = os.getenv("HASS_URL")
            JOB_START_TIME = os.getenv("JOB_START_TIME","07:00" )
            LOG_LEVEL = os.getenv("LOG_LEVEL","INFO")
            VERSION = os.getenv("VERSION")
            RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
            RUN_AT_START = os.getenv("RUN_AT_START","true").lower() == "true"
            
            logger_init(LOG_LEVEL)
            logging.info(f"当前以 Docker 镜像方式运行。")
        except Exception as e:
            logging.error(f"读取 .env 失败，程序将退出，原因: {e}。")
            sys.exit()

    logging.info(f"当前仓库版本: {VERSION}")
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"当前时间: {current_datetime}。")

    logging.info(f"开始初始化 ErrorWatcher。")
    ErrorWatcher.init(root_dir='/data/errors')
    logging.info(f'ErrorWatcher 初始化完成。')
    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)

    # 生成随机延迟时间（-10分钟到+10分钟）
    random_delay_minutes = random.randint(-10, 10)
    parsed_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(minutes=random_delay_minutes)
    logging.info(f"当前账号: {PHONE_NUMBER}，Home Assistant 地址: {HASS_URL}，每日执行时间: {parsed_time.strftime('%H:%M')}。")

    # 添加随机延迟
    next_run_time = parsed_time + timedelta(hours=12)

    logging.info(f'每日计划两次执行，时间 {parsed_time.strftime("%H:%M")} 和 {next_run_time.strftime("%H:%M")}')
    schedule.every().day.at(parsed_time.strftime("%H:%M")).do(run_task, fetcher)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher)
    if RUN_AT_START:
        logging.info('RUN_AT_START=true，启动即执行一次任务。')
        run_task(fetcher)
    else:
        logging.info('RUN_AT_START=false，启动时不立即执行任务。')

    while True:
        schedule.run_pending()
        time.sleep(1)


def run_task(data_fetcher: DataFetcher):
    for retry_times in range(1, RETRY_TIMES_LIMIT + 1):
        try:
            data_fetcher.fetch()
            return
        except Exception as e:
            logging.error(f"任务失败: {e}，剩余重试 {RETRY_TIMES_LIMIT - retry_times} 次。")
            continue

def logger_init(level: str):
    logger = logging.getLogger()
    logger.setLevel(level)
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    format = logging.Formatter("%(asctime)s  [%(levelname)-8s] ---- %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(format)
    logger.addHandler(sh)


if __name__ == "__main__":
    main()
