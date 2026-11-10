"""Record actual Docker identity/state/limits for the fixed check script."""
from pathlib import Path
import json
import re
import subprocess
import sys

identity, destination = sys.argv[1:]
if re.fullmatch(r"[a-f0-9]{64}", identity) is None:
    raise ValueError("Expected an owned container identity")
container = json.loads(subprocess.check_output(["docker", "inspect", identity]))[0]
host = container["HostConfig"]; state = container["State"]
record = {"container_id": container["Id"], "image_id": container["Image"],
          "exit": state["ExitCode"], "OOMKilled": state["OOMKilled"],
          "started_at": state["StartedAt"], "finished_at": state["FinishedAt"],
          "user": container["Config"]["User"], "network": host["NetworkMode"],
          "host_binds": host["Binds"], "cap_drop": host["CapDrop"],
          "security_opt": host["SecurityOpt"], "pids_limit": host["PidsLimit"],
          "memory_bytes": host["Memory"], "cpu_nanocores": host["NanoCpus"],
          "read_only_root": host["ReadonlyRootfs"]}
if not (record["user"] == "1000:1000" and record["network"] == "none"
        and not record["host_binds"] and record["cap_drop"] == ["ALL"]
        and record["security_opt"] == ["no-new-privileges"]
        and record["pids_limit"] == 256 and record["memory_bytes"] == 1073741824
        and record["cpu_nanocores"] == 2000000000):
    raise ValueError("Actual container limits do not match the contract")
Path(destination).write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, sort_keys=True))
