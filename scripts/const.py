# 填写普通参数 不要填写密码等敏感信息
# 国网电力官网
LOGIN_URL = "https://95598.cn/osgweb/login"
ELECTRIC_USAGE_URL = "https://95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://95598.cn/osgweb/userAcc"


# Home Assistant
SUPERVISOR_URL = "http://supervisor/core"
API_PATH = "/api/states/" # https://developers.home-assistant.io/docs/api/rest/

BALANCE_SENSOR_NAME = "sensor.electricity_charge_balance"
DAILY_USAGE_SENSOR_NAME = "sensor.last_electricity_usage"
YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_electricity_usage"
YEARLY_CHARGE_SENSOR_NAME = "sensor.yearly_electricity_charge"
MONTH_USAGE_SENSOR_NAME = "sensor.month_electricity_usage"
MONTH_CHARGE_SENSOR_NAME = "sensor.month_electricity_charge"
BALANCE_UNIT = "CNY"
USAGE_UNIT = "KWH"

# 新增分项/分月实体
YESTERDAY_VALLEY_SENSOR_NAME = "sensor.yesterday_valley_usage"
YESTERDAY_FLAT_SENSOR_NAME = "sensor.yesterday_flat_usage"
YESTERDAY_PEAK_SENSOR_NAME = "sensor.yesterday_peak_usage"
YESTERDAY_SHARP_SENSOR_NAME = "sensor.yesterday_sharp_usage"

MONTH_TOTAL_SENSOR_NAME = "sensor.current_month_total_usage"
MONTH_VALLEY_SENSOR_NAME = "sensor.current_month_valley_usage"
MONTH_FLAT_SENSOR_NAME = "sensor.current_month_flat_usage"
MONTH_PEAK_SENSOR_NAME = "sensor.current_month_peak_usage"
MONTH_SHARP_SENSOR_NAME = "sensor.current_month_sharp_usage"

FIRST_DAY_HISTORY_SENSOR_NAME = "sensor.current_month_first_day_history"

