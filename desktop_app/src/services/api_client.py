
import requests
import logging
import threading
from typing import Any

class APIClient:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self.base_url = "http://127.0.0.1:5000"
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._request_lock = threading.Lock()

    def _headers(self) -> dict:
        if self.access_token:
            return {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
        return {"Content-Type": "application/json"}

    def _request(self, method: str, endpoint: str, **kwargs) -> dict | list | None:
        try:
            with self._request_lock:
                response = requests.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    headers=self._headers(),
                    timeout=10,
                    **kwargs
                )

                if response.status_code == 401 and self.refresh_token:
                    if self._do_refresh():
                        # Retry once with new token
                        response = requests.request(
                            method,
                            f"{self.base_url}{endpoint}",
                            headers=self._headers(),
                            timeout=10,
                            **kwargs
                        )
                    else:
                        return None

                if response.status_code >= 400:
                    logging.error(f"API error {response.status_code}: {response.text}")
                    return None

                if response.status_code == 204:
                    return {}

                return response.json()

        except requests.exceptions.ConnectionError:
            logging.error("No se puede conectar con el backend")
            return None
        except Exception as e:
            logging.error(f"Error en request: {e}")
            return None

    def _do_refresh(self) -> bool:
        try:
            headers = {"Authorization": f"Bearer {self.refresh_token}"}
            response = requests.post(
                f"{self.base_url}/api/v1/auth/refresh",
                headers=headers,
                timeout=10
            )
            if response.ok:
                data = response.json()
                self.access_token = data.get("access_token")
                return True
            return False
        except Exception:
            return False

    # Auth methods
    def login(self, username: str, password: str) -> dict | None:
        response = self._request(
            "POST",
            "/api/v1/auth/login",
            json={"username": username, "password": password}
        )
        if response:
            self.access_token = response.get("access_token")
            self.refresh_token = response.get("refresh_token")
        return response

    def logout(self) -> None:
        self.access_token = None
        self.refresh_token = None

    def is_authenticated(self) -> bool:
        return self.access_token is not None

    # Camera methods
    def get_cameras(self) -> list[dict]:
        result = self._request("GET", "/api/v1/cameras/")
        return result if result else []

    def get_camera(self, camera_id: int) -> dict | None:
        return self._request("GET", f"/api/v1/cameras/{camera_id}")

    def add_camera(self, data: dict) -> dict | None:
        return self._request("POST", "/api/v1/cameras/", json=data)

    def update_camera(self, camera_id: int, data: dict) -> dict | None:
        return self._request("PUT", f"/api/v1/cameras/{camera_id}", json=data)

    def delete_camera(self, camera_id: int) -> bool:
        return self._request("DELETE", f"/api/v1/cameras/{camera_id}") is not None

    def toggle_camera(self, camera_id: int, active: bool) -> dict | None:
        return self._request("PATCH", f"/api/v1/cameras/{camera_id}/toggle", json={"active": active})

    def discover_cameras(self) -> list[dict]:
        result = self._request("POST", "/api/v1/cameras/discover")
        return result if result else []

    def get_stream_url(self, camera_id: int) -> str:
        token = self.access_token or ""
        return f"{self.base_url}/api/v1/cameras/{camera_id}/stream?token={token}"

    def get_capabilities(self, camera_id: int) -> dict | None:
        return self._request("GET", f"/api/v1/cameras/{camera_id}/capabilities")

    # PTZ methods
    def ptz_move(self, camera_id: int, direction: str, speed: float = 0.5) -> bool:
        result = self._request(
            "POST",
            f"/api/v1/cameras/{camera_id}/ptz/move",
            json={"direction": direction, "speed": speed}
        )
        return result is not None

    def ptz_stop(self, camera_id: int) -> bool:
        result = self._request("POST", f"/api/v1/cameras/{camera_id}/ptz/stop")
        return result is not None

    def get_ptz_presets(self, camera_id: int) -> list[dict]:
        result = self._request("GET", f"/api/v1/cameras/{camera_id}/ptz/presets")
        return result if result else []

    def go_to_preset(self, camera_id: int, token: str) -> bool:
        result = self._request(
            "POST",
            f"/api/v1/cameras/{camera_id}/ptz/presets/{token}/goto"
        )
        return result is not None

    # LED and Audio methods
    def set_leds(self, camera_id: int, mode: str) -> bool:
        result = self._request(
            "POST",
            f"/api/v1/cameras/{camera_id}/leds",
            json={"mode": mode}
        )
        return result is not None

    def start_audio(self, camera_id: int) -> bool:
        result = self._request("POST", f"/api/v1/cameras/{camera_id}/audio/start")
        return result is not None

    def stop_audio(self, camera_id: int) -> bool:
        result = self._request("POST", f"/api/v1/cameras/{camera_id}/audio/stop")
        return result is not None

    # Event methods
    def get_events(
        self,
        camera_id: int | None = None,
        event_type: str | None = None,
        hours: int = 24,
        limit: int = 50
    ) -> list[dict]:
        params = {"hours": hours, "limit": limit}
        if camera_id is not None:
            params["camera_id"] = camera_id
        if event_type:
            params["event_type"] = event_type

        result = self._request("GET", "/api/v1/events/", params=params)
        return result if result else []

    def acknowledge_event(self, event_id: int) -> bool:
        result = self._request("PATCH", f"/api/v1/events/{event_id}/acknowledge")
        return result is not None

    def get_event_stats(self) -> dict | None:
        return self._request("GET", "/api/v1/events/stats")

    # Recording methods
    def get_recordings(self, camera_id: int | None = None, limit: int = 100) -> list[dict]:
        params = {"limit": limit}
        if camera_id is not None:
            params["camera_id"] = camera_id
        result = self._request("GET", "/api/v1/recordings/", params=params)
        return result if result else []

    def get_download_url(self, recording_id: int) -> str:
        token = self.access_token or ""
        return f"{self.base_url}/api/v1/recordings/{recording_id}/download?token={token}"

    def delete_recording(self, recording_id: int) -> bool:
        return self._request("DELETE", f"/api/v1/recordings/{recording_id}") is not None

    # Config methods
    def get_system_stats(self) -> dict | None:
        return self._request("GET", "/api/v1/system/stats")

    def get_config(self) -> dict | None:
        return self._request("GET", "/api/v1/system/config")

    def update_config(self, config: dict) -> dict | None:
        return self._request("PUT", "/api/v1/system/config", json=config)


# Global instance
api_client = APIClient()
