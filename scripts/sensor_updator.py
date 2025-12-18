import logging
import os
from datetime import datetime,timedelta

import requests
from sympy import true

from const import *


class SensorUpdator:

    def __init__(self):
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        self.base_url = HASS_URL[:-1] if HASS_URL.endswith("/") else HASS_URL
        self.token = HASS_TOKEN
        self.RECHARGE_NOTIFY = os.getenv("RECHARGE_NOTIFY", "false").lower() == "true"

    def update_one_userid(
        self,
        user_id: str,
        balance: float,
        last_daily_date: str,
        last_daily_usage: float,
        yearly_charge: float,
        yearly_usage: float,
        month_charge: float,
        month_usage: float,
        yesterday_tou=None,
        month_tou=None,
        first_day_history=None,
    ):
        postfix = f"_{user_id[-4:]}"
        if balance is not None:
            self.balance_notify(user_id, balance)
            self.update_balance(postfix, balance)
        if last_daily_usage is not None:
            self.update_last_daily_usage(postfix, last_daily_date, last_daily_usage)
        if yearly_usage is not None:
            self.update_yearly_data(postfix, yearly_usage, usage=True)
        if yearly_charge is not None:
            self.update_yearly_data(postfix, yearly_charge)
        if month_usage is not None:
            self.update_month_data(postfix, month_usage, usage=True)
        if month_charge is not None:
            self.update_month_data(postfix, month_charge)

        # 新增昨日分时段用电
        if yesterday_tou:
            self.update_yesterday_tou(postfix, yesterday_tou)

        # 新增当月分时段用电
        if month_tou:
            self.update_month_tou(postfix, month_tou)

        # 记录本月1号用电历史
        if first_day_history:
            self.update_first_day_history(postfix, first_day_history)

        logging.info(f"User {user_id} state-refresh task run successfully!")

    def update_yesterday_tou(self, postfix: str, tou: dict):
        mapping = [
            (YESTERDAY_VALLEY_SENSOR_NAME, tou.get("valley"), "谷用电"),
            (YESTERDAY_FLAT_SENSOR_NAME, tou.get("flat"), "平用电"),
            (YESTERDAY_PEAK_SENSOR_NAME, tou.get("peak"), "峰用电"),
            (YESTERDAY_SHARP_SENSOR_NAME, tou.get("sharp"), "尖用电"),
        ]
        for sensor_base, value, label in mapping:
            if value is None:
                continue
            sensorName = sensor_base + postfix
            request_body = {
                "state": value,
                "unique_id": sensorName,
                "attributes": {
                    "last_reset": tou.get("date"),
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:lightning-bolt",
                    "device_class": "energy",
                    "state_class": "measurement",
                    "description": f"{label} ({tou.get('date')})",
                },
            }
            self.send_url(sensorName, request_body)
            logging.info(f"Homeassistant sensor {sensorName} state updated: {value} kWh")

    def update_month_tou(self, postfix: str, tou: dict):
        mapping = [
            (MONTH_TOTAL_SENSOR_NAME, tou.get("total"), "kWh"),
            (MONTH_VALLEY_SENSOR_NAME, tou.get("valley"), "kWh"),
            (MONTH_FLAT_SENSOR_NAME, tou.get("flat"), "kWh"),
            (MONTH_PEAK_SENSOR_NAME, tou.get("peak"), "kWh"),
            (MONTH_SHARP_SENSOR_NAME, tou.get("sharp"), "kWh"),
        ]

        # 月度 last_reset 取上月最后一天，保持递增语义
        current_date = datetime.now()
        first_day_of_current_month = current_date.replace(day=1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        last_reset = last_day_of_previous_month.strftime("%Y-%m-%d")

        for sensor_base, value, unit in mapping:
            if value is None:
                continue
            sensorName = sensor_base + postfix
            request_body = {
                "state": value,
                "unique_id": sensorName,
                "attributes": {
                    "last_reset": last_reset,
                    "unit_of_measurement": unit,
                    "icon": "mdi:lightning-bolt",
                    "device_class": "energy",
                    "state_class": "total_increasing",
                },
            }
            self.send_url(sensorName, request_body)
            logging.info(f"Homeassistant sensor {sensorName} state updated: {value} {unit}")

    def update_first_day_history(self, postfix: str, first_day: dict):
        sensorName = FIRST_DAY_HISTORY_SENSOR_NAME + postfix
        # State 保存 CSV 样式文本，方便在 HA 前端查看
        state_text = f"{first_day.get('total', '')},{first_day.get('valley', '')},{first_day.get('flat', '')},{first_day.get('peak', '')},{first_day.get('sharp', '')}"
        request_body = {
            "state": state_text,
            "unique_id": sensorName,
            "attributes": {
                "date": first_day.get("date"),
                "fields": "日用电,日谷电,日平电,日峰电,日尖电",
                "unit_of_measurement": "kWh",
            },
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {state_text}")

    def update_last_daily_usage(self, postfix: str, last_daily_date: str, sensorState: float):
        sensorName = DAILY_USAGE_SENSOR_NAME + postfix
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_daily_date,
                "unit_of_measurement": "kWh",
                "icon": "mdi:lightning-bolt",
                "device_class": "energy",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} kWh")

    def update_balance(self, postfix: str, sensorState: float):
        sensorName = BALANCE_SENSOR_NAME + postfix
        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "CNY",
                "icon": "mdi:cash",
                "device_class": "monetary",
                "state_class": "total",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} CNY")

    def update_month_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            MONTH_USAGE_SENSOR_NAME + postfix
            if usage
            else MONTH_CHARGE_SENSOR_NAME + postfix
        )
        current_date = datetime.now()
        first_day_of_current_month = current_date.replace(day=1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        last_reset = last_day_of_previous_month.strftime("%Y-%m")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} {'kWh' if usage else 'CNY'}")

    def update_yearly_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            YEARLY_USAGE_SENSOR_NAME + postfix
            if usage
            else YEARLY_CHARGE_SENSOR_NAME + postfix
        )
        if datetime.now().month == 1:
            last_year = datetime.now().year -1 
            last_reset = datetime.now().replace(year=last_year).strftime("%Y")
        else:
            last_reset = datetime.now().strftime("%Y")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "total_increasing",
            },
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} {'kWh' if usage else 'CNY'}")

    def send_url(self, sensorName, request_body):
        headers = {
            "Content-Type": "application-json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensorName  # /api/states/<entity_id>
        try:
            response = requests.post(url, json=request_body, headers=headers)
            logging.debug(
                f"Homeassistant REST API invoke, POST on {url}. response[{response.status_code}]: {response.content}"
            )
        except Exception as e:
            logging.error(f"Homeassistant REST API invoke failed, reason is {e}")

    def balance_notify(self, user_id, balance):

        if self.RECHARGE_NOTIFY :
            BALANCE = float(os.getenv("BALANCE", 10.0))
            PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN").split(",")        
            logging.info(f"Check the electricity bill balance. When the balance is less than {BALANCE} CNY, the notification will be sent = {self.RECHARGE_NOTIFY}")
            if balance < BALANCE :
                for token in PUSHPLUS_TOKEN:
                    title = "电费余额不足提醒"
                    content = (f"您用户号{user_id}的当前电费余额为：{balance}元，请及时充值。" )
                    url = ("http://www.pushplus.plus/send?token="+ token+ "&title="+ title+ "&content="+ content)
                    requests.get(url)
                    logging.info(
                        f"The current balance of user id {user_id} is {balance} CNY less than {BALANCE} CNY, notice has been sent, please pay attention to check and recharge."
                    )
        else :
            logging.info(
            f"Check the electricity bill balance, the notification will be sent = {self.RECHARGE_NOTIFY}")
            return

