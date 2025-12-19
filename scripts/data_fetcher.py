import logging
import os
import re
import subprocess
import time

import shutil

import random
import base64
import sqlite3
from datetime import datetime
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.common.exceptions import TimeoutException
from sensor_updator import SensorUpdator
from error_watcher import ErrorWatcher

from const import *

import numpy as np
# import cv2
from io import BytesIO
from PIL import Image
from onnx import ONNX
import platform


def base64_to_PLI(base64_str: str):
    base64_data = re.sub('^data:image/.+;base64,', '', base64_str)
    byte_data = base64.b64decode(base64_data)
    image_data = BytesIO(byte_data)
    img = Image.open(image_data)
    return img

def get_transparency_location(image):
    '''获取基于透明元素裁切图片的左上角、右下角坐标

    :param image: cv2加载好的图像
    :return: (left, upper, right, lower)元组
    '''
    # 1. 扫描获得最左边透明点和最右边透明点坐标
    height, width, channel = image.shape  # 高、宽、通道数
    assert channel == 4  # 无透明通道报错
    first_location = None  # 最先遇到的透明点
    last_location = None  # 最后遇到的透明点
    first_transparency = []  # 从左往右最先遇到的透明点，元素个数小于等于图像高度
    last_transparency = []  # 从左往右最后遇到的透明点，元素个数小于等于图像高度
    for y, rows in enumerate(image):
        for x, BGRA in enumerate(rows):
            alpha = BGRA[3]
            if alpha != 0:
                if not first_location or first_location[1] != y:  # 透明点未赋值或为同一列
                    first_location = (x, y)  # 更新最先遇到的透明点
                    first_transparency.append(first_location)
                last_location = (x, y)  # 更新最后遇到的透明点
        if last_location:
            last_transparency.append(last_location)

    # 2. 矩形四个边的中点
    top = first_transparency[0]
    bottom = first_transparency[-1]
    left = None
    right = None
    for first, last in zip(first_transparency, last_transparency):
        if not left:
            left = first
        if not right:
            right = last
        if first[0] < left[0]:
            left = first
        if last[0] > right[0]:
            right = last

    # 3. 左上角、右下角
    upper_left = (left[0], top[1])  # 左上角
    bottom_right = (right[0], bottom[1])  # 右下角

    return upper_left[0], upper_left[1], bottom_right[0], bottom_right[1]

class DataFetcher:

    def __init__(self, username: str, password: str):
        if 'PYTHON_IN_DOCKER' not in os.environ: 
            import dotenv
            dotenv.load_dotenv(verbose=True)
        self._username = username
        self._password = password
        self.onnx = ONNX("./captcha.onnx")

        # 获取 ENABLE_DATABASE_STORAGE 的值，默认为 False
        self.enable_database_storage = os.getenv("ENABLE_DATABASE_STORAGE", "false").lower() == "true"
        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 60))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
        # Faster waits for inner table expansion to avoid long per-row delays
        self.DETAIL_WAIT_TIME = max(1, min(self.RETRY_WAIT_TIME_OFFSET_UNIT, 3))
        # 等待滑块图片加载时间，防止空白导致 distance=0
        self.SLIDER_IMAGE_WAIT = max(1, min(self.RETRY_WAIT_TIME_OFFSET_UNIT, 5))
        self.SNAPSHOT_DIR = "/config/gwkz"
        self.snapshot_session_dir = None
        self.IGNORE_USER_ID = os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",")

    # @staticmethod
    def _click_button(self, driver, button_search_type, button_search_key):
        '''wrapped click function, click only when the element is clickable'''
        click_element = driver.find_element(button_search_type, button_search_key)
        # logging.info(f"click_element:{button_search_key}.is_displayed() = {click_element.is_displayed()}\r")
        # logging.info(f"click_element:{button_search_key}.is_enabled() = {click_element.is_enabled()}\r")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)

    # @staticmethod
    def _is_captcha_legal(self, captcha):
        ''' check the ddddocr result, justify whether it's legal'''
        if (len(captcha) != 4):
            return False
        for s in captcha:
            if (not s.isalpha() and not s.isdigit()):
                return False
        return True

    # @staticmethod 
    def _sliding_track(self, driver, distance):# 机器模拟人工滑动轨迹
        # 获取按钮
        slider = driver.find_element(By.CLASS_NAME, "slide-verify-slider-mask-item")
        ActionChains(driver).click_and_hold(slider).perform()
        # 获取轨迹
        # tracks = _get_tracks(distance)
        # for t in tracks:
        yoffset_random = random.uniform(-2, 4)
        ActionChains(driver).move_by_offset(xoffset=distance, yoffset=yoffset_random).perform()
            # time.sleep(0.2)
        ActionChains(driver).release().perform()

    def connect_user_db(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            DB_NAME = os.getenv("DB_NAME", "homeassistant.db")
            if 'PYTHON_IN_DOCKER' in os.environ: 
                DB_NAME = "/data/" + DB_NAME
            self.connect = sqlite3.connect(DB_NAME)
            self.connect.cursor()
            logging.info(f"数据库 {DB_NAME} 创建成功。")
            # 创建表名
            self.table_name = f"daily{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS {self.table_name} (
                    date DATE PRIMARY KEY NOT NULL, 
                    usage REAL NOT NULL)'''
            self.connect.execute(sql)
            logging.info(f"数据表 {self.table_name} 创建成功。")
			
			# 创建data表名
            self.table_expand_name = f"data{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS {self.table_expand_name} (
                    name TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL)'''
            self.connect.execute(sql)
            logging.info(f"扩展表 {self.table_expand_name} 创建成功。")
			
        # 如果表已存在，则不会创建
        except sqlite3.Error as e:
            logging.debug(f"创建数据库/表出错: {e}")
            return False
        return True

    def insert_data(self, data:dict):
        if self.connect is None:
            logging.error("数据库连接未建立。")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_name} VALUES(strftime('%Y-%m-%d','{data['date']}'),{data['usage']});"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"数据写入失败: {e}")

    def insert_expand_data(self, data:dict):
        if self.connect is None:
            logging.error("数据库连接未建立。")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_expand_name} VALUES('{data['name']}','{data['value']}');"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"数据写入失败: {e}")
                
    def _get_webdriver(self):
        if platform.system() == 'Windows':
            driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()))
        else:
            firefox_options = webdriver.FirefoxOptions()
            firefox_options.add_argument('--incognito')
            firefox_options.add_argument("--start-maximized")
            firefox_options.add_argument('--headless')
            firefox_options.add_argument('--no-sandbox')
            firefox_options.add_argument('--disable-gpu')
            firefox_options.add_argument('--disable-dev-shm-usage')
            logging.info(f"启动 Firefox 浏览器。\r")
            gecko_path = os.getenv("GECKODRIVER_PATH") or shutil.which("geckodriver") or "/usr/local/bin/geckodriver"
            if not os.path.exists(gecko_path):
                raise FileNotFoundError(f"Geckodriver not found at {gecko_path}; set GECKODRIVER_PATH or ensure it is on PATH.")
            driver = webdriver.Firefox(options=firefox_options, service=FirefoxService(gecko_path))
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)
        return driver

    def _dump_snapshot(self, driver, prefix: str):
        """保存当前页面截图到 /config/gwkz，便于调试。"""
        try:
            base_dir = self.snapshot_session_dir or self.SNAPSHOT_DIR
            os.makedirs(base_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = os.path.join(base_dir, f"{prefix}_{ts}.png")
            driver.save_screenshot(png_path)
            logging.info(f"已保存页面截图: {png_path}")
        except Exception as e:
            logging.debug(f"保存页面截图失败: {e}")

    def _restore_login_context(self, driver):
        """刷新后重新回到账号密码登录并填充表单、点击登录，避免停留在扫码页。"""
        try:
            driver.get(LOGIN_URL)
            time.sleep(self.DETAIL_WAIT_TIME)
            driver.find_element(By.CLASS_NAME, "user").click()
            time.sleep(0.5)
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
            time.sleep(0.5)
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
            time.sleep(0.5)
            inputs = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            if len(inputs) >= 2:
                inputs[0].clear(); inputs[0].send_keys(self._username)
                inputs[1].clear(); inputs[1].send_keys(self._password)
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            time.sleep(self.DETAIL_WAIT_TIME)
            logging.info("刷新后已回到账密登录并重新点击登录，等待滑块。")
        except Exception as e:
            logging.warning(f"刷新后恢复登录环境失败: {e}")

    def _is_logged_in(self, driver):
        """判断是否已登录，优先检查跳转，其次检查主页元素。"""
        if LOGIN_URL not in driver.current_url:
            return True
        try:
            driver.find_element(By.CLASS_NAME, "el-dropdown")
            return True
        except Exception:
            return False

    def _wait_login_success(self, driver):
        """等待登录成功信号，避免拖动后尚未跳转就误判失败。"""
        wait_seconds = max(self.LOGIN_EXPECTED_TIME, self.DETAIL_WAIT_TIME)
        try:
            WebDriverWait(driver, wait_seconds).until(lambda d: self._is_logged_in(d))
            return True
        except TimeoutException:
            return False

    @ErrorWatcher.watch
    def _login(self, driver, phone_code = False):
        try:
            driver.get(LOGIN_URL)
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of_element_located((By.CLASS_NAME, "user")))
        except:
            logging.debug(f"登录页打开失败，无法访问 {LOGIN_URL}。")
        logging.info(f"打开登录页 {LOGIN_URL}。\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
        # swtich to username-password login page
        driver.find_element(By.CLASS_NAME, "user").click()
        logging.info("切换到账号密码登录页。\r")
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # click agree button
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("点击同意协议。\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        if phone_code:
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"输入手机号: {self._username}\r")
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
            code = input("Input your phone verification code: ")
            input_elements[3].send_keys(code)
            logging.info(f"输入短信验证码: {code}.\r")
            # click login button
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("点击登录按钮。\r")

            return True
        else :
            # input username and password
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[0].send_keys(self._username)
            logging.info(f"输入手机号: {self._username}\r")
            input_elements[1].send_keys(self._password)
            logging.info(f"输入密码: {self._password}\r")
            logging.info("账号密码已填，准备点击登录并处理滑块。")

            # click login button
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("点击登录按钮。\r")
            # sometimes ddddOCR may fail, so add retry logic)
            for retry_times in range(1, self.RETRY_TIMES_LIMIT + 1):
                self._dump_snapshot(driver, f"slider_attempt_{retry_times}")
                logging.info(f"开始滑块尝试 {retry_times}/{self.RETRY_TIMES_LIMIT}。")
                # 进入滑块模式前先确认入口存在，避免元素缺失导致报错
                try:
                    slider_tab = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                        EC.presence_of_element_located((By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span'))
                    )
                    try:
                        WebDriverWait(driver, self.DETAIL_WAIT_TIME).until(EC.element_to_be_clickable(slider_tab))
                        slider_tab.click()
                    except Exception:
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", slider_tab)
                        driver.execute_script("arguments[0].click();", slider_tab)
                except Exception as tab_err:
                    logging.warning(f"未找到滑块入口，刷新重试: {tab_err}")
                    self._restore_login_context(driver)
                    continue

                # 等待滑块弹窗与画布可见，避免图片未加载
                try:
                    WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                        EC.visibility_of_element_located((By.ID, "slideVerify"))
                    )
                except Exception as modal_err:
                    logging.warning(f"滑块弹窗未出现，刷新重试: {modal_err}")
                    self._restore_login_context(driver)
                    continue
                time.sleep(self.SLIDER_IMAGE_WAIT)

                #get canvas image
                background_JS = 'return document.getElementById("slideVerify").childNodes[0].toDataURL("image/png");'
                # targe_JS = 'return document.getElementsByClassName("slide-verify-block")[0].toDataURL("image/png");'
                # get base64 image data
                im_info = driver.execute_script(background_JS) 
                background = im_info.split(',')[1]  
                background_image = base64_to_PLI(background)
                logging.info(f"获取滑块背景图成功。\r")
                distance = self.onnx.get_distance(background_image)
                logging.info(f"识别滑块缺口距离: {distance}。\r")

                # 模型返回 0 视为未识别，直接重试，避免无效拖动
                if distance == 0:
                    logging.warning("滑块缺口未识别到，刷新验证码重试。")
                    try:
                        refresh_btn = None
                        for selector in ["#slideVerify .slide-verify-refresh-icon", ".slide-verify-refresh-icon",
                                         "#slideVerify .el-icon-refresh", "//*[@id='slideVerify']//i[contains(@class,'refresh')]"]:
                            try:
                                if selector.startswith("//"):
                                    refresh_btn = driver.find_element(By.XPATH, selector)
                                elif selector.startswith("#") or selector.startswith("."):
                                    refresh_btn = driver.find_element(By.CSS_SELECTOR, selector)
                                if refresh_btn:
                                    break
                            except Exception:
                                continue
                        if refresh_btn:
                            driver.execute_script("arguments[0].click();", refresh_btn)
                            time.sleep(self.SLIDER_IMAGE_WAIT)
                        else:
                            logging.debug("未找到刷新按钮，改为重新加载登录页。")
                            self._restore_login_context(driver)
                    except Exception as refresh_err:
                        logging.debug(f"刷新验证码失败，改为重新加载登录页: {refresh_err}")
                        self._restore_login_context(driver)
                    continue

                self._sliding_track(driver, round(distance*1.06)) #1.06是补偿
                time.sleep(self.DETAIL_WAIT_TIME)
                logging.info("已拖动滑块，检查登录结果。")
                if self._wait_login_success(driver):
                    logging.info("滑块验证通过，检测到登录成功。")
                    self._dump_snapshot(driver, "after_login_success")
                    return True

                # 未检测到登录成功，点击登录或刷新后重试
                try:
                    logging.info("滑块校验失败或未跳转，尝试重新点击登录再试。\r")
                    self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                except Exception:
                    logging.debug(
                        f"重新点击登录失败，刷新页面重试，剩余 {self.RETRY_TIMES_LIMIT - retry_times} 次重试。")
                    self._restore_login_context(driver)
                continue
            logging.error(f"登录失败，可能因滑块校验未通过。")
        return False

        raise Exception(
            "Login failed, maybe caused by 1.incorrect phone_number and password, please double check. or 2. network, please mnodify LOGIN_EXPECTED_TIME in .env and run docker compose up --build.")
        
    def fetch(self):

        """main logic here"""

        driver = self._get_webdriver()
        ErrorWatcher.instance().set_driver(driver)

        # 为本次任务创建独立截图目录
        self.snapshot_session_dir = os.path.join(
            self.SNAPSHOT_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        os.makedirs(self.snapshot_session_dir, exist_ok=True)
        
        driver.maximize_window() 
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        logging.info("浏览器驱动初始化完成。")
        updator = SensorUpdator()
        
        try:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                if self._login(driver,phone_code=True):
                    logging.info("登录成功！")
                else:
                    logging.info("登录失败！")
                    raise Exception("login unsuccessed")
            else:
                if self._login(driver):
                    logging.info("登录成功！")
                else:
                    logging.info("登录失败！")
                    raise Exception("login unsuccessed")
        except Exception as e:
            logging.error(
                f"浏览器异常退出，原因: {e}，剩余 {self.RETRY_TIMES_LIMIT} 次重试。")
            driver.quit()
            return

        logging.info(f"已登录: {LOGIN_URL}")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        logging.info(f"开始获取户号列表。")
        user_id_list = self._get_user_ids(driver)
        logging.info(f"共 {len(user_id_list)} 个户号: {user_id_list}，其中 {self.IGNORE_USER_ID} 将被忽略。")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)


        for userid_index, user_id in enumerate(user_id_list):           
            try: 
                # switch to electricity charge balance page
                driver.get(BALANCE_URL) 
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                self._choose_current_userid(driver,userid_index)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                current_userid = self._get_current_userid(driver)
                if current_userid in self.IGNORE_USER_ID:
                    logging.info(f"户号 {current_userid} 在忽略列表中，跳过。")
                    continue
                else:
                    ### get data 
                    balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, yesterday_tou, month_tou, first_day_history  = self._get_all_data(driver, user_id, userid_index)
                    updator.update_one_userid(
                        user_id,
                        balance,
                        last_daily_date,
                        last_daily_usage,
                        yearly_charge,
                        yearly_usage,
                        month_charge,
                        month_usage,
                        yesterday_tou,
                        month_tou,
                        first_day_history,
                    )
        
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            except Exception as e:
                if (userid_index != len(user_id_list)):
                    logging.info(f"户号 {user_id} 拉取失败 {e}，继续下一个。")
                else:
                    logging.info(f"户号 {user_id} 拉取失败，错误: {e}")
                    logging.info("数据拉取结束，关闭浏览器。")
                continue    

        driver.quit()


    def _get_current_userid(self, driver):
        current_userid = driver.find_element(By.XPATH, '//*[@id="app"]/div/div/article/div/div/div[2]/div/div/div[1]/div[2]/div/div/div/div[2]/div/div[1]/div/ul/div/li[1]/span[2]').text
        return current_userid
    
    def _choose_current_userid(self, driver, userid_index):
        elements = driver.find_elements(By.CLASS_NAME, "button_confirm")
        if elements:
            self._click_button(driver, By.XPATH, f'''//*[@id="app"]/div/div[2]/div/div/div/div[2]/div[2]/div/button''')
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        self._click_button(driver, By.XPATH, f"/html/body/div[2]/div[1]/div[1]/ul/li[{userid_index+1}]/span")
        

    def _get_all_data(self, driver, user_id, userid_index):
        balance = self._get_electric_balance(driver)
        if (balance is None):
            logging.info(f"获取户号 {user_id} 余额失败，跳过。")
        else:
            logging.info(
                f"获取户号 {user_id} 余额成功，余额 {balance} 元。")
        self._dump_snapshot(driver, f"balance_{user_id}")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # swithc to electricity usage page
        driver.get(ELECTRIC_USAGE_URL)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        self._choose_current_userid(driver, userid_index)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # get data for each user id
        yearly_usage, yearly_charge = self._get_yearly_data(driver)

        if yearly_usage is None:
            logging.error(f"获取户号 {user_id} 年用电量失败，跳过。")
        else:
            logging.info(
                f"获取户号 {user_id} 年用电量成功，用电 {yearly_usage} kWh。")
        if yearly_charge is None:
            logging.error(f"获取户号 {user_id} 年电费失败，跳过。")
        else:
            logging.info(
                f"获取户号 {user_id} 年电费成功，费用 {yearly_charge} 元。")

        # 按月获取数据
        month, month_usage, month_charge = self._get_month_usage(driver)
        if month is None:
            logging.error(f"获取户号 {user_id} 月用电失败，跳过。")
        else:
            for m in range(len(month)):
                logging.info(f"获取户号 {user_id} {month[m]} 数据成功，用电 {month_usage[m]} kWh，电费 {month_charge[m]} 元。")
        # 近30天日用电（含谷/平/峰/尖）
        daily_records = self._get_daily_usage_data(driver)
        last_daily_date = None
        last_daily_usage = None
        yesterday_tou = None
        if daily_records:
            last_daily_date = daily_records[0].get("date")
            last_daily_usage = daily_records[0].get("total")
            yesterday_tou = {
                "date": daily_records[0].get("date"),
                "valley": daily_records[0].get("valley"),
                "flat": daily_records[0].get("flat"),
                "peak": daily_records[0].get("peak"),
                "sharp": daily_records[0].get("sharp"),
            }

        if last_daily_usage is None:
            logging.error(f"获取户号 {user_id} 日用电失败，跳过。")
        else:
            logging.info(
                f"获取户号 {user_id} 日用电成功，{last_daily_date} 用电 {last_daily_usage} kWh。")
        if yesterday_tou:
            logging.info(
                f"昨日分时: 日期={yesterday_tou.get('date')}, 谷={yesterday_tou.get('valley')}, 平={yesterday_tou.get('flat')}, 峰={yesterday_tou.get('peak')}, 尖={yesterday_tou.get('sharp')}"
            )
        if month is None:
            logging.error(f"获取户号 {user_id} 月用电失败，跳过。")

        # 当月分时段汇总（仅当前月）
        month_tou = None
        today = datetime.now()
        if daily_records:
            month_tou = {"total": 0.0, "valley": 0.0, "flat": 0.0, "peak": 0.0, "sharp": 0.0}
            for record in daily_records:
                try:
                    record_date = datetime.strptime(record.get("date"), "%Y-%m-%d")
                except Exception:
                    continue
                if record_date.month == today.month and record_date.year == today.year:
                    if record.get("total") is not None:
                        month_tou["total"] += record.get("total")
                    for key in ["valley", "flat", "peak", "sharp"]:
                        if record.get(key) is not None:
                            month_tou[key] += record.get(key)
            logging.info(
                f"本月分时汇总: 总={month_tou.get('total')}, 谷={month_tou.get('valley')}, 平={month_tou.get('flat')}, 峰={month_tou.get('peak')}, 尖={month_tou.get('sharp')}"
            )

        # 找到当月1号的记录，存在则上报历史
        first_day_history = None
        for record in daily_records:
            try:
                record_date = datetime.strptime(record.get("date"), "%Y-%m-%d")
            except Exception:
                continue
            if record_date.day == 1 and record_date.month == today.month and record_date.year == today.year:
                first_day_history = {
                    "date": record.get("date"),
                    "total": record.get("total"),
                    "valley": record.get("valley"),
                    "flat": record.get("flat"),
                    "peak": record.get("peak"),
                    "sharp": record.get("sharp"),
                }
                break

        # 新增储存用电量
        if self.enable_database_storage:
            logging.info("已启用数据库持久化，开始写入数据。")
            date = [r.get("date") for r in daily_records if r.get("date")]
            usages = [r.get("total") for r in daily_records if r.get("total") is not None]
            self._save_user_data(user_id, balance, last_daily_date, last_daily_usage, date, usages, month, month_usage, month_charge, yearly_charge, yearly_usage)
        else:
            logging.info("未启用数据库持久化，跳过数据写入。")

        if month_charge:
            month_charge = month_charge[-1]
        else:
            month_charge = None
        if month_usage:
            month_usage = month_usage[-1]
        else:
            month_usage = None

        return balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, yesterday_tou, month_tou, first_day_history

    def _get_user_ids(self, driver):
        try:
            # 刷新网页
            driver.refresh()
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.presence_of_element_located((By.CLASS_NAME, 'el-dropdown')))
            # click roll down button for user id
            self._click_button(driver, By.XPATH, "//div[@class='el-dropdown']/span")
            logging.debug(f"点击户号下拉按钮。")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for roll down menu displayed
            target = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_element(By.TAG_NAME, "li")
            logging.debug(f"获取下拉菜单首个选项。")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            logging.debug(f"等待下拉菜单可见。")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                EC.text_to_be_present_in_element((By.XPATH, "//ul[@class='el-dropdown-menu el-popper']/li"), ":"))
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

            # get user id one by one
            userid_elements = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_elements(By.TAG_NAME, "li")
            userid_list = []
            for element in userid_elements:
                userid_list.append(re.findall("[0-9]+", element.text)[-1])
            return userid_list
        except Exception as e:
            logging.error(
                f"浏览器异常退出，获取户号列表失败，原因: {e}。")
            driver.quit()

    def _get_electric_balance(self, driver):
        try:
            balance = driver.find_element(By.CLASS_NAME, "num").text
            balance_text = driver.find_element(By.CLASS_NAME, "amttxt").text
            if "欠费" in balance_text :
                return -float(balance)
            else:
                return float(balance)
        except:
            return None

    def _get_yearly_data(self, driver):

        try:
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                span_element = driver.find_element(By.XPATH, f"//span[contains(text(), '{datetime.now().year - 1}')]")
                span_element.click()
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for data displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
        except Exception as e:
            logging.error(f"年数据获取失败: {e}")
            return None, None

        # get data
        try:
            yearly_usage = driver.find_element(By.XPATH, "//ul[@class='total']/li[1]/span").text
        except Exception as e:
            logging.error(f"年用电量获取失败: {e}")
            yearly_usage = None

        try:
            yearly_charge = driver.find_element(By.XPATH, "//ul[@class='total']/li[2]/span").text
        except Exception as e:
            logging.error(f"年电费获取失败: {e}")
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self, driver):
        """获取最近一次用电量"""
        try:
            # 点击日用电量
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for data displayed
            usage_element = driver.find_element(By.XPATH,
                                                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(usage_element)) # 等待用电量出现

            # 增加是哪一天
            date_element = driver.find_element(By.XPATH,
                                                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[1]/div")
            last_daily_date = date_element.text # 获取最近一次用电量的日期
            return last_daily_date, float(usage_element.text)
        except Exception as e:
            logging.error(f"昨日数据获取失败: {e}")
            return None

    def _get_month_usage(self, driver):
        """获取每月用电量"""

        try:
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                span_element = driver.find_element(By.XPATH, f"//span[contains(text(), '{datetime.now().year - 1}')]")
                span_element.click()
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for month displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
            month_element = driver.find_element(By.XPATH, "//*[@id='pane-first']/div[1]/div[2]/div[2]/div/div[3]/table/tbody").text
            month_element = month_element.split("\n")
            month_element.remove("MAX")
            month_element = np.array(month_element).reshape(-1, 3)
            # 将每月的用电量保存为List
            month = []
            usage = []
            charge = []
            for i in range(len(month_element)):
                month.append(month_element[i][0])
                usage.append(month_element[i][1])
                charge.append(month_element[i][2])
            return month, usage, charge
        except Exception as e:
            logging.error(f"月数据获取失败: {e}")
            return None,None,None

    # 获取近30天每日用电量及分时段（谷/平/峰/尖）
    def _get_daily_usage_data(self, driver):
        records = []
        logging.info("切换到日用电(近30天)标签。")
        self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
        time.sleep(self.DETAIL_WAIT_TIME)

        # 强制切到近30天
        try:
            self._click_button(driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[2]/span[1]")
        except Exception:
            # 兼容只有一个选项的情况
            self._click_button(driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[1]/span[1]")
        time.sleep(self.DETAIL_WAIT_TIME)
        logging.info("日用电标签就绪，等待数据行出现。")

        # 页面上方是折线图，真实表格在下方，需要滚动到表格区域再等待行出现
        try:
            chart_anchor = driver.find_element(By.XPATH, "//div[@class='el-tab-pane dayd']//div[contains(@class,'echarts')]//canvas | //div[@class='el-tab-pane dayd']//div[contains(@class,'chart')]")
            driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", chart_anchor)
            time.sleep(0.5)
            driver.execute_script("window.scrollBy(0, 600);")
        except Exception:
            # 即便找不到锚点也继续，让后续等待去兜底
            driver.execute_script("window.scrollBy(0, 800);")
            time.sleep(0.5)

        # 等待第一行出现（表格在下方，需滚动后再等）
        first_row = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, "//div[@class='el-tab-pane dayd']//table/tbody/tr[contains(@class,'el-table__row') and not(contains(@class,'el-table__expanded-row'))]"))
        )
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(first_row))

        rows = driver.find_elements(
            By.XPATH,
            "//div[@class='el-tab-pane dayd']//table/tbody/tr[contains(@class,'el-table__row') and not(contains(@class,'el-table__expanded-row'))]",
        )
        if not rows:
            self._dump_snapshot(driver, "daily_table_not_found_after_scroll")
            logging.debug("未找到日数据行，已截图 daily_table_not_found_after_scroll。")
        logging.info(f"检测到 {len(rows)} 条日数据，开始解析。")

        for row in rows:
            row_start = time.perf_counter()
            try:
                day_text = row.find_element(By.XPATH, "td[1]/div").text
                total_text = row.find_element(By.XPATH, "td[2]/div").text
                total = float(total_text) if total_text else None
            except Exception as e:
                logging.debug(f"因解析错误跳过一行日数据: {e}")
                continue

            valley = flat = peak = sharp = None
            took_detail_snapshot = False
            # 展开当日详情获取谷/平/峰/尖（需点击行最右侧的箭头按钮）
            try:
                expand_btn = row.find_element(
                    By.XPATH,
                    "(.//button[contains(@class,'el-table__expand-icon')] | .//span[contains(@class,'el-table__expand-icon')] | .//td[last()]//*[contains(@class,'arrow') or contains(@class,'caret') or contains(@class,'el-icon')])[1]",
                )
            except Exception:
                expand_btn = None

            if not expand_btn:
                # 无展开按钮也截个图方便排查 DOM 结构
                if not took_detail_snapshot:
                    self._dump_snapshot(driver, f"daily_detail_{day_text}_no_expand_btn")
                    took_detail_snapshot = True
                logging.debug(f"未找到 {day_text} 的展开按钮，跳过谷/平/峰/尖解析。")
            else:
                for attempt in range(2):
                    try:
                        logging.info(f"1")
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", expand_btn)
                        time.sleep(0.5)
                        try:
                            logging.info(f"2")
                            WebDriverWait(driver, self.DETAIL_WAIT_TIME).until(EC.element_to_be_clickable(expand_btn))
                            expand_btn.click()
                        except Exception:
                            logging.info(f"2-e")
                            driver.execute_script("arguments[0].click();", expand_btn)

                        if not took_detail_snapshot:
                            self._dump_snapshot(driver, f"daily_detail_{day_text}_attempt{attempt+1}")
                            took_detail_snapshot = True

                        try:
                            logging.info(f"3")
                            WebDriverWait(driver, self.DETAIL_WAIT_TIME).until(
                                lambda d: len(row.find_elements(By.XPATH, "following-sibling::tr[1][contains(@class,'el-table__expanded-row')]") ) > 0
                            )
                        except Exception:
                            logging.info(f"3-e")
                            continue

                        try:
                            logging.info(f"4")
                            driver.implicitly_wait(0)
                            detail_rows = row.find_elements(By.XPATH, "following-sibling::tr[1][contains(@class,'el-table__expanded-row')]")
                        finally:
                            logging.info(f"4-f")
                            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)

                        if not detail_rows:
                            if attempt == 1:
                                self._dump_snapshot(driver, f"daily_detail_{day_text}_no_detail_rows")
                            continue

                        logging.info(f"5")
                        detail_row = detail_rows[0]
                        detail_items = detail_row.find_elements(By.XPATH, ".//li")
                        if not detail_items and attempt == 1:
                            self._dump_snapshot(driver, f"daily_detail_{day_text}_no_detail_items")

                        # 先尝试从 li 里提取
                        for item in detail_items:
                            text = item.text
                            number_match = re.search(r"([0-9]+\.?[0-9]*)", text)
                            if not number_match:
                                continue
                            value = float(number_match.group(1))
                            logging.info(f"6: {text} -> {value}")
                            if "谷" in text:
                                valley = value
                            elif "平" in text:
                                flat = value
                            elif "峰" in text:
                                peak = value
                            elif "尖" in text:
                                sharp = value

                        # 部分页面使用 div/span 文本（如 “谷用电：16.44”），增加直接搜索关键词兜底
                        def _extract_by_keyword(keyword: str):
                            xpath_candidates = [
                                f".//*[contains(text(), '{keyword}')]",
                                f".//span[contains(text(), '{keyword}')]",
                                f".//div[contains(text(), '{keyword}')]",
                            ]
                            for xp in xpath_candidates:
                                for elem in detail_row.find_elements(By.XPATH, xp):
                                    m = re.search(r"([0-9]+\.?[0-9]*)", elem.text)
                                    if m:
                                        return float(m.group(1))
                            return None

                        valley = valley if valley is not None else _extract_by_keyword("谷用电")
                        flat = flat if flat is not None else _extract_by_keyword("平用电")
                        peak = peak if peak is not None else _extract_by_keyword("峰用电")
                        sharp = sharp if sharp is not None else _extract_by_keyword("尖用电")
                        logging.info(f"6-2")
                        break
                    except Exception as inner_e:
                        logging.debug(f"展开 {day_text} 尝试 {attempt+1} 失败: {inner_e}")
                        if attempt == 1:
                            if not took_detail_snapshot:
                                logging.info(f"7")
                                self._dump_snapshot(driver, f"daily_detail_{day_text}_expand_failed")
                            raise
                        time.sleep(1)
                else:
                    if not took_detail_snapshot:
                        logging.info(f"8")
                        self._dump_snapshot(driver, f"daily_detail_{day_text}_expand_unresolved")
                        took_detail_snapshot = True

            record = {
                "date": day_text,
                "total": total,
                "valley": valley,
                "flat": flat,
                "peak": peak,
                "sharp": sharp,
            }
            duration = time.perf_counter() - row_start
            logging.info(
                f"日记录: 日期={day_text}, 总={total}, 谷={valley}, 平={flat}, 峰={peak}, 尖={sharp}, 耗时={duration:.2f}s"
            )
            records.append(record)
        return records

    def _save_user_data(self, user_id, balance, last_daily_date, last_daily_usage, date, usages, month, month_usage, month_charge, yearly_charge, yearly_usage):
        # 连接数据库集合
        if self.connect_user_db(user_id):
            # 写入当前户号
            dic = {'name': 'user', 'value': f"{user_id}"}
            self.insert_expand_data(dic)
            # 写入剩余金额
            dic = {'name': 'balance', 'value': f"{balance}"}
            self.insert_expand_data(dic)
            # 写入最近一次更新时间
            dic = {'name': f"daily_date", 'value': f"{last_daily_date}"}
            self.insert_expand_data(dic)
            # 写入最近一次更新时间用电量
            dic = {'name': f"daily_usage", 'value': f"{last_daily_usage}"}
            self.insert_expand_data(dic)
            
            # 写入年用电量
            dic = {'name': 'yearly_usage', 'value': f"{yearly_usage}"}
            self.insert_expand_data(dic)
            # 写入年用电电费
            dic = {'name': 'yearly_charge', 'value': f"{yearly_charge} "}
            self.insert_expand_data(dic)
            
            for index in range(len(date)):
                dic = {'date': date[index], 'usage': float(usages[index])}
                # 插入到数据库
                try:
                    self.insert_data(dic)
                    logging.info(f"已写入 {date[index]} 用电 {usages[index]}KWh 到数据库。")
                except Exception as e:
                    logging.debug(f"写入 {date[index]} 用电失败，可能已存在: {str(e)}")

            for index in range(len(month)):
                try:
                    dic = {'name': f"{month[index]}usage", 'value': f"{month_usage[index]}"}
                    self.insert_expand_data(dic)
                    dic = {'name': f"{month[index]}charge", 'value': f"{month_charge[index]}"}
                    self.insert_expand_data(dic)
                except Exception as e:
                    logging.debug(f"写入 {month[index]} 月度数据失败，可能已存在: {str(e)}")
            if month_charge:
                month_charge = month_charge[-1]
            else:
                month_charge = None
                
            if month_usage:
                month_usage = month_usage[-1]
            else:
                month_usage = None
            # 写入本月电量
            dic = {'name': f"month_usage", 'value': f"{month_usage}"}
            self.insert_expand_data(dic)
            # 写入本月电费
            dic = {'name': f"month_charge", 'value': f"{month_charge}"}
            self.insert_expand_data(dic)
            # dic = {'date': month[index], 'usage': float(month_usage[index]), 'charge': float(month_charge[index])}
            self.connect.close()
        else:
            logging.info("数据库创建失败，数据未写入。")
            return

if __name__ == "__main__":
    with open("bg.jpg", "rb") as f:
        test1 = f.read()
        print(type(test1))
        print(test1)
