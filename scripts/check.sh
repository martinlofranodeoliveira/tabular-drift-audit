#!/bin/sh
set -eu
mkdir -p build
docker build -f Dockerfile.prepare -t tabular-drift-prepared:local .
docker build --network none --target sdk -t tabular-drift-sdk:local .
sdk_identity=$(docker create --network none --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --memory 1g --cpus 2 tabular-drift-sdk:local)
if docker start --attach "$sdk_identity"; then sdk_exit=0; else sdk_exit=$?; fi
python3 scripts/record_container.py "$sdk_identity" build/sdk-container.json
if [ "$sdk_exit" -ne 0 ]; then docker rm "$sdk_identity"; exit "$sdk_exit"; fi
docker cp "$sdk_identity:/work/results" build/
docker rm "$sdk_identity"
docker build --network none --target runtime -t tabular-drift-runtime:local .
consumer_identity=$(docker create --network none --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --memory 1g --cpus 2 \
  --read-only --tmpfs /work/tmp:uid=1000,gid=1000,mode=700,size=16777216 \
  --entrypoint /opt/runtime/bin/python tabular-drift-runtime:local -I /work/installed_consumer.py)
if docker start --attach "$consumer_identity"; then consumer_exit=0; else consumer_exit=$?; fi
python3 scripts/record_container.py "$consumer_identity" build/runtime-consumer.json
docker rm "$consumer_identity"
if [ "$consumer_exit" -ne 0 ]; then exit "$consumer_exit"; fi
demo_identity=$(docker create --network none --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 --memory 1g --cpus 2 \
  --read-only --tmpfs /work/tmp:uid=1000,gid=1000,mode=700,size=16777216 \
  tabular-drift-runtime:local demo --assert)
if docker start --attach "$demo_identity"; then demo_exit=0; else demo_exit=$?; fi
python3 scripts/record_container.py "$demo_identity" build/runtime-demo.json
docker rm "$demo_identity"
exit "$demo_exit"
