from slowapi import Limiter

from app.http.client_ip import get_client_ip

limiter = Limiter(key_func=get_client_ip, default_limits=[])
