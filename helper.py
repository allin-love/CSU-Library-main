import argparse
import ast
import base64
import configparser
import csv
import getpass
import json
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


BASE_URL = "https://libzw.csu.edu.cn"
CAS_START_URL = f"{BASE_URL}/v4/login/cas"
SHA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"
V4_IV = "ZZWBKJ_ZHIHUAWEI"


class CSUError(RuntimeError):
    """可直接展示给用户的错误。"""


class ConfigError(CSUError):
    pass


class LoginError(CSUError):
    pass


class APIError(CSUError):
    def __init__(self, message: str, code: Any = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SeatPreference:
    no: str
    area_id: str
    cached_id: str


@dataclass
class AreaResult:
    area_id: str
    reserve_type: int
    day_info: dict[str, Any]
    seats: list[dict[str, Any]]
    display_time: str


def random_string(length: int) -> str:
    return "".join(secrets.choice(AES_CHARS) for _ in range(length))


def aes_cbc_encrypt(plain_text: str, key: str, iv: str) -> str:
    """匹配官网使用的 AES-CBC、PKCS7、Base64 格式。"""
    padder = PKCS7(128).padder()
    padded = padder.update(plain_text.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(key.strip().encode("utf-8")),
        modes.CBC(iv.encode("utf-8")),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def encrypt_cas_password(password: str, salt: str) -> str:
    # 官网会在真实密码前加入 64 个随机字符，并使用随机 IV。
    return aes_cbc_encrypt(random_string(64) + password, salt, random_string(16))


def encrypt_v4_payload(data: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(SHA_TZ)
    date_key = now.strftime("%Y%m%d")
    key = date_key + date_key[::-1]
    plain = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return aes_cbc_encrypt(plain, key, V4_IV)


def _literal_or_text(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value.strip()


def _time_value(value: Any) -> str:
    """从新版接口的时段对象中提取可读时间。"""
    if isinstance(value, dict):
        start = value.get("start_time") or value.get("start")
        end = value.get("end_time") or value.get("end")
        if start and end:
            return f"{start}–{end}"
        return str(value.get("name") or value.get("id") or "")
    return str(value or "")


class CSULibrary:
    def __init__(
        self,
        userid: str | None = None,
        password: str | None = None,
        config_path: str | Path = "config.ini",
        session: requests.Session | None = None,
    ) -> None:
        self.userid = userid
        self.password = password
        self.config_path = Path(config_path).resolve()
        self.client = session or requests.Session()
        self.client.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.token: str | None = None
        self.campus, self.preferences, self.days_ahead = self._load_config()

    def _load_config(self) -> tuple[str, list[SeatPreference], int]:
        parser = configparser.ConfigParser()
        if not parser.read(self.config_path, encoding="utf-8"):
            raise ConfigError(f"找不到配置文件：{self.config_path}")
        if "DATABASE" not in parser:
            raise ConfigError("config.ini 缺少 [DATABASE] 配置段")

        section = parser["DATABASE"]
        campus = str(_literal_or_text(section.get("CAMPUS", ""))).strip()
        raw_seats = _literal_or_text(section.get("SEAT", "[]"))
        if not campus:
            raise ConfigError("CAMPUS 不能为空")
        if not isinstance(raw_seats, (list, tuple)) or not raw_seats:
            raise ConfigError("SEAT 必须是至少包含一个座位号的列表")
        seat_numbers = [str(value).strip() for value in raw_seats]

        try:
            days_ahead = section.getint("DAYS_AHEAD", fallback=1)
        except ValueError as exc:
            raise ConfigError("DAYS_AHEAD 必须是整数") from exc
        if days_ahead not in (0, 1):
            raise ConfigError("当前系统只开放今天和明天，DAYS_AHEAD 只能是 0 或 1")

        seat_file = self.config_path.parent / f"{campus}座位表.csv"
        if not seat_file.exists():
            raise ConfigError(f"找不到座位表：{seat_file}")
        with seat_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

        by_number = {str(row.get("NO", "")).strip(): row for row in rows}
        missing = [number for number in seat_numbers if number not in by_number]
        if missing:
            raise ConfigError(f"以下座位号不在 {seat_file.name} 中：{', '.join(missing)}")

        preferences = [
            SeatPreference(
                no=number,
                area_id=str(by_number[number].get("AREA", "")).strip(),
                cached_id=str(by_number[number].get("ID", "")).strip(),
            )
            for number in seat_numbers
        ]
        if any(not item.area_id for item in preferences):
            raise ConfigError("座位表中存在缺少 AREA 的目标座位")
        return campus, preferences, days_ahead

    @property
    def target_day(self) -> str:
        return (datetime.now(SHA_TZ).date() + timedelta(days=self.days_ahead)).isoformat()

    def _decode_json(self, response: requests.Response, purpose: str) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise APIError(f"{purpose}失败：HTTP {response.status_code}") from exc
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise APIError(f"{purpose}失败：服务器没有返回 JSON") from exc
        if not isinstance(payload, dict):
            raise APIError(f"{purpose}失败：返回结构异常")
        return payload

    def _api_post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        encrypted: bool = False,
        purpose: str = "请求接口",
    ) -> Any:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth:
            if not self.token:
                raise LoginError("尚未登录，不能访问该接口")
            # 官网前端的格式是 bearer 后面不加空格。
            headers["authorization"] = f"bearer{self.token}"
        body: dict[str, Any] = data or {}
        if encrypted:
            body = {"aesjson": encrypt_v4_payload(body)}
        try:
            response = self.client.post(
                urljoin(BASE_URL, path), json=body, headers=headers, timeout=25
            )
        except requests.RequestException as exc:
            raise APIError(f"{purpose}失败：{exc}") from exc
        payload = self._decode_json(response, purpose)
        if payload.get("code") != 0:
            message = payload.get("message") or payload.get("msg") or "未知错误"
            raise APIError(f"{purpose}失败：{message}", payload.get("code"))
        return payload.get("data") or {}

    def probe(self) -> dict[str, Any]:
        data = self._api_post(
            "/v4/space/index", {}, auth=False, purpose="检查新版预约接口"
        )
        premises = data.get("premises", [])
        dates = data.get("date", [])
        if not premises or not dates:
            raise APIError("新版预约接口可访问，但没有返回校区或开放日期")
        return {"premises": premises, "dates": dates}

    @staticmethod
    def _captcha_required(session: requests.Session, login_url: str, userid: str) -> bool:
        url = urljoin(login_url, "/authserver/checkNeedCaptcha.htl")
        try:
            response = session.get(url, params={"username": userid}, timeout=15)
            response.raise_for_status()
            return bool(response.json().get("isNeed"))
        except (requests.RequestException, ValueError):
            # 这是预检查；后续仍会识别返回的验证码登录页。
            return False

    @staticmethod
    def _extract_cas_value(response: requests.Response) -> str | None:
        candidates = [response.url, response.text]
        candidates.extend(item.headers.get("Location", "") for item in response.history)
        for candidate in candidates:
            if not candidate:
                continue
            decoded = unquote(candidate)
            match = re.search(r"[?&]cas=([^&#\"'<>\s]+)", decoded)
            if match:
                return match.group(1)
            parsed = urlparse(decoded)
            if "?" in parsed.fragment:
                values = parse_qs(parsed.fragment.split("?", 1)[1]).get("cas")
                if values:
                    return values[0]
        return None

    @staticmethod
    def _login_page_error(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for selector in (
            "#showErrorTip",
            ".auth_error",
            ".login-error",
            ".item-error-tip",
            ".errors",
        ):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return None

    def login(self) -> dict[str, Any]:
        if not self.userid or not self.password:
            raise LoginError("登录测试需要校园卡账号和统一身份认证密码")
        try:
            page = self.client.get(CAS_START_URL, timeout=25, allow_redirects=True)
            page.raise_for_status()
        except requests.RequestException as exc:
            raise LoginError(f"无法打开统一身份认证页面：{exc}") from exc

        soup = BeautifulSoup(page.text, "html.parser")
        form = soup.select_one("form#pwdFromId")
        if form is None:
            raise LoginError("统一身份认证页面结构已改变：找不到密码登录表单")
        salt_node = form.select_one("#pwdEncryptSalt")
        execution_node = form.select_one('input[name="execution"]')
        if salt_node is None or execution_node is None:
            raise LoginError("统一身份认证页面结构已改变：缺少加密参数")
        salt = salt_node.get("value", "")
        execution = execution_node.get("value", "")

        if self._captcha_required(self.client, page.url, self.userid):
            raise LoginError(
                "统一身份认证当前要求验证码，自动预约无法安全继续。"
                "请先在浏览器成功登录一次，稍后再运行 test。"
            )

        form_data = {
            "username": self.userid,
            "password": encrypt_cas_password(self.password, salt),
            "captcha": "",
            "_eventId": "submit",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "lt": "",
            "execution": execution,
        }
        try:
            result = self.client.post(
                page.url,
                data=form_data,
                headers={"Referer": page.url},
                timeout=30,
                allow_redirects=True,
            )
            result.raise_for_status()
        except requests.RequestException as exc:
            raise LoginError(f"统一身份认证请求失败：{exc}") from exc

        cas_value = self._extract_cas_value(result)
        if not cas_value:
            page_error = self._login_page_error(result.text)
            detail = f"：{page_error}" if page_error else ""
            raise LoginError(f"统一身份认证未通过{detail}。请核对账号密码或验证码状态")

        member_data = self._api_post(
            "/v4/login/user",
            {"cas": cas_value},
            auth=False,
            purpose="换取预约系统登录令牌",
        )
        member = member_data.get("member") or {}
        token = member.get("token")
        if not token:
            raise LoginError("统一身份认证成功，但预约系统没有返回 token")
        self.token = str(token)
        return member

    @staticmethod
    def _default_custom_times(day_info: dict[str, Any]) -> tuple[str, str]:
        start = day_info.get("def_start_time") or day_info.get("start_time")
        end = day_info.get("def_end_time") or day_info.get("end_time")
        if not start or not end:
            raise APIError("服务器没有返回默认预约起止时间")
        return str(start)[-5:], str(end)[-5:]

    def _query_area(self, area_id: str) -> AreaResult:
        info = self._api_post(
            "/v4/Space/map", {"id": area_id}, purpose=f"读取区域 {area_id}"
        )
        date_config = info.get("date") or {}
        try:
            reserve_type = int(date_config.get("reserveType", 1))
        except (TypeError, ValueError):
            reserve_type = 1
        date_rows = date_config.get("list") or []
        day_info = next(
            (row for row in date_rows if str(row.get("day")) == self.target_day), None
        )
        if not day_info:
            raise APIError(f"区域 {area_id} 当前不开放 {self.target_day} 的预约")

        query = {
            "id": area_id,
            "day": self.target_day,
            "label_id": [],
            "start_time": "",
            "end_time": "",
            "begdate": "",
            "enddate": "",
        }
        display_time = ""
        times = day_info.get("times") or []
        if reserve_type == 1:
            if not times:
                raise APIError(f"区域 {area_id} 没有固定预约时段")
            selected_time = times[0]
            # 固定 segment 在最终确认时只使用 segment.id，但
            # /Space/seat 列表查询仍要同时传该时段的 start/end。
            # 这与官网前端 filterSearch.times.start/end 的行为一致。
            if isinstance(selected_time, dict):
                query["start_time"] = selected_time.get("start") or selected_time.get(
                    "start_time", ""
                )
                query["end_time"] = selected_time.get("end") or selected_time.get(
                    "end_time", ""
                )
            display_time = _time_value(selected_time)
        elif reserve_type == 2:
            if not times:
                raise APIError(f"区域 {area_id} 没有可用预约时段")
            selected_time = times[0]
            query["start_time"] = selected_time
            query["end_time"] = selected_time
            display_time = _time_value(selected_time)
        else:
            start, end = self._default_custom_times(day_info)
            query["start_time"] = start
            query["end_time"] = end
            display_time = f"{start}–{end}"

        seat_data = self._api_post(
            "/v4/Space/seat", query, purpose=f"查询区域 {area_id} 座位"
        )
        seats = seat_data.get("list") or []
        if not isinstance(seats, list):
            raise APIError(f"区域 {area_id} 的座位列表格式异常")
        return AreaResult(area_id, reserve_type, day_info, seats, display_time)

    def inspect_preferences(self) -> list[dict[str, Any]]:
        results_by_area: dict[str, AreaResult] = {}
        errors_by_area: dict[str, str] = {}
        for area_id in dict.fromkeys(item.area_id for item in self.preferences):
            try:
                results_by_area[area_id] = self._query_area(area_id)
            except CSUError as exc:
                errors_by_area[area_id] = str(exc)

        inspected: list[dict[str, Any]] = []
        for preference in self.preferences:
            area_result = results_by_area.get(preference.area_id)
            if not area_result:
                inspected.append(
                    {
                        "preference": preference,
                        "seat": None,
                        "area": None,
                        "error": errors_by_area.get(preference.area_id, "区域查询失败"),
                    }
                )
                continue
            seat = next(
                (
                    value
                    for value in area_result.seats
                    if str(value.get("no", "")).casefold() == preference.no.casefold()
                ),
                None,
            )
            inspected.append(
                {
                    "preference": preference,
                    "seat": seat,
                    "area": area_result,
                    "error": None if seat else "新版座位列表中未找到该座位号",
                }
            )
        return inspected

    def test(self) -> list[dict[str, Any]]:
        self.probe()
        member = self.login()
        display_name = member.get("name") or member.get("username") or self.userid
        logging.info("登录成功：%s", display_name)
        inspected = self.inspect_preferences()
        found = [item for item in inspected if item["seat"] is not None]
        if not found:
            details = "; ".join(
                f"{item['preference'].no}: {item['error']}" for item in inspected
            )
            raise APIError(f"登录成功，但没有找到任何目标座位。{details}")
        return inspected

    @staticmethod
    def _confirm_payload(item: dict[str, Any], target_day: str) -> dict[str, Any]:
        seat = item["seat"]
        area: AreaResult = item["area"]
        payload: dict[str, Any] = {
            "seat_id": seat.get("id"),
            "segment": "",
            "day": target_day,
            "start_time": "",
            "end_time": "",
        }
        times = area.day_info.get("times") or []
        if area.reserve_type == 1:
            if not times:
                raise APIError("目标日期没有固定预约时段")
            first = times[0]
            payload["segment"] = first.get("id") if isinstance(first, dict) else first
        elif area.reserve_type == 2:
            if not times:
                raise APIError("目标日期没有可预约时段")
            payload["end_time"] = times[0]
        else:
            start, end = CSULibrary._default_custom_times(area.day_info)
            payload["start_time"] = start
            payload["end_time"] = end
        return payload

    def reserve(self) -> dict[str, Any]:
        self.probe()
        self.login()
        inspected = self.inspect_preferences()
        available = [
            item
            for item in inspected
            if item["seat"] is not None and int(item["seat"].get("status", 0)) == 1
        ]
        if not available:
            found = [item for item in inspected if item["seat"] is not None]
            if found:
                raise APIError("目标座位均已被占用或当前不可预约")
            raise APIError("新版座位列表中没有找到配置的目标座位")

        selected = available[0]
        payload = self._confirm_payload(selected, self.target_day)
        result = self._api_post(
            "/v4/space/confirm",
            payload,
            encrypted=True,
            purpose=f"预约座位 {selected['preference'].no}",
        )
        return {
            "seat": selected["preference"].no,
            "time": selected["area"].display_time,
            "result": result,
        }


def resolve_credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    userid = args.userid or os.environ.get("CSU_USER")
    password = args.password or os.environ.get("CSU_PASSWORD")
    if args.action in ("test", "reserve") and sys.stdin.isatty():
        if not userid:
            userid = input("校园卡账号：").strip()
        if not password:
            password = getpass.getpass("统一身份认证密码：")
    return userid, password


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    file_handler = logging.FileHandler("library.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="中南大学图书馆新版座位预约助手")
    parser.add_argument(
        "--action",
        choices=("probe", "test", "reserve"),
        default="probe",
        help="probe=公开接口检查；test=登录并查询座位但不预约；reserve=正式预约",
    )
    parser.add_argument("--userid", help="校园卡账号；也可使用 CSU_USER 环境变量")
    parser.add_argument(
        "--password", help="统一身份认证密码；建议使用 CSU_PASSWORD 环境变量或交互输入"
    )
    parser.add_argument("--config", default="config.ini", help="配置文件路径")
    return parser


def main() -> int:
    configure_logging()
    args = build_parser().parse_args()
    userid, password = resolve_credentials(args)
    try:
        helper = CSULibrary(userid, password, args.config)
        if args.action == "probe":
            result = helper.probe()
            names = ", ".join(str(item.get("name")) for item in result["premises"])
            logging.info("新版公开接口正常；开放日期：%s；校区：%s", result["dates"], names)
        elif args.action == "test":
            inspected = helper.test()
            logging.info("DRY-RUN：未调用预约确认接口")
            for item in inspected:
                seat = item["seat"]
                if seat is None:
                    logging.warning("座位 %s：%s", item["preference"].no, item["error"])
                else:
                    status = "可预约" if int(seat.get("status", 0)) == 1 else "不可预约"
                    logging.info(
                        "座位 %s：已找到（id=%s，状态=%s，时段=%s）",
                        item["preference"].no,
                        seat.get("id"),
                        status,
                        item["area"].display_time,
                    )
            logging.info("测试通过：登录和座位查询可用，未产生预约")
        else:
            result = helper.reserve()
            logging.info(
                "预约成功：%s，时段=%s；返回：%s",
                result["seat"],
                result["time"],
                result["result"],
            )
        return 0
    except CSUError as exc:
        logging.error("%s", exc)
        return 1
    except Exception:
        logging.exception("未预期错误")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
