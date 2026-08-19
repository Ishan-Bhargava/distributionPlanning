"""
Writes containers.json for `aws lightsail create-container-service-deployment`.

Done in Python rather than sed/envsubst because OPTIFLOW_USERS_JSON is itself a
JSON blob - embedding JSON-inside-JSON safely needs a real JSON encoder, not
text substitution, or a stray quote in the accounts file breaks the container
spec.

Usage: python3 make_containers_json.py <image-ref> <out-path>
Reads OPTIFLOW_API_URL, OPTIFLOW_API_KEY, OPTIFLOW_USERS_JSON from the
environment (set as GitLab CI/CD variables - never committed).
"""

import json
import os
import sys

image_ref, out_path = sys.argv[1], sys.argv[2]

containers = {
    "app": {
        "image": image_ref,
        "ports": {"8501": "HTTP"},
        "environment": {
            "OPTIFLOW_API_URL": os.environ.get("OPTIFLOW_API_URL", ""),
            "OPTIFLOW_API_KEY": os.environ.get("OPTIFLOW_API_KEY", ""),
            "OPTIFLOW_USERS_JSON": os.environ.get("OPTIFLOW_USERS_JSON", ""),
        },
    }
}

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(containers, f)

print(f"wrote {out_path} for image {image_ref}")
