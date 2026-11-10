"""Write, read and delete one Firestore document to confirm Admin SDK access."""

import json
import sys

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import load_settings


def main() -> None:
    settings = load_settings()
    try:
        cert = credentials.Certificate(
            json.loads(settings.firebase_service_account_json)
        )
    except ValueError as exc:
        # Only the exception type is shown so key material never reaches the terminal.
        sys.exit(f"Invalid FIREBASE_SERVICE_ACCOUNT_JSON ({type(exc).__name__})")
    app = firebase_admin.initialize_app(
        cert, {"projectId": settings.firebase_project_id}
    )
    db = firestore.client(app)
    ref = db.collection("_healthcheck").document("ping")
    ref.set({"checkedAt": firestore.SERVER_TIMESTAMP})
    exists = ref.get().exists
    ref.delete()
    if not exists:
        sys.exit("Firestore read returned no document")
    print(settings.firebase_project_id)
    print("Firestore OK")


if __name__ == "__main__":
    main()
