"""
Получение списка сетевых интерфейсов сервера.
"""

import psutil
import socket


def get_interfaces() -> list[dict]:
    """
    Возвращает список сетевых интерфейсов с их IPv4-адресами.
    Пример: [{"name": "Ethernet", "ip": "192.168.1.10"}, ...]
    """
    result = []
    try:
        stats = psutil.net_if_addrs()
        for name, addrs in stats.items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    result.append({
                        "name": name,
                        "ip": addr.address,
                    })
    except Exception:
        pass
    return result