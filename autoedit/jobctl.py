"""작업 중지 요청. 오래 걸리는 반복문에서 check()를 불러 중지 요청이 있으면 Cancelled를 던진다."""
import threading


class Cancelled(Exception):
    pass


_flag = threading.Event()


def request():
    _flag.set()


def clear():
    _flag.clear()


def check():
    if _flag.is_set():
        raise Cancelled("사용자가 작업을 중지했습니다.")
