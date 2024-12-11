import datetime
import pytz
from langchain_core.tools import tool

@tool
def get_time(timezone: str, format: str):
    """获取指定时区当前时间，并根据指定的格式返回时间字符串。
    Args:
        timezone: 时区名称字符串。例如 "Asia/Shanghai"， "America/New_York"。
        format: 时间格式字符串。
    """
    if timezone is None:
        timezone = "Asia/Shanghai"
    if format is None:
        format = "%Y-%m-%d %H:%M:%S"

    try:
        tz = pytz.timezone(timezone)
        now = datetime.datetime.now(tz)
        return now.strftime(format)
    except pytz.exceptions.UnknownTimeZoneError:
        print(f"警告：无效的时区名称 '{timezone}'，将使用 UTC 时间。")
        tz = pytz.timezone("UTC")
        now = datetime.datetime.now(tz)
        return now.strftime(format)

tools = [get_time]