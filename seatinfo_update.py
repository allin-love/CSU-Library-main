"""兼容入口：旧版 v3 座位表下载接口已经下线。"""


def main() -> int:
    print(
        "旧版 /api.php/v3areas 与 /spaces_old 接口已下线。\n"
        "新版 helper.py 会在每次 test/reserve 时实时查询座位状态，"
        "不需要再运行 seatinfo_update.py 更新状态列。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
