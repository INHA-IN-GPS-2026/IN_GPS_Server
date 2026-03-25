# IN-GPS (Server, AWS-ec2)

IN-GPS server 초기버전입니다. 
현재 Gateway -> Broker(mosquitto) -> Server -> web API(FAST API)
까지 진해오디었습니다.

## Project Structure
 - app.py
 - db.py
 - requirement.txt
 - mqtt_subscriber.py
 - 
## Features
- Gateway <-> Broker -> DB -> web API 


## Requirements
- Python: 3.x.x.x
- mySql
- Server always active
- mosquitto broker

## Check Swagger
- (http://13.209.92.219:8000/docs#/)
- 
## Build & Flash
- (EC2 인증서가필요함)
- windows powershell start
- ssh -i ingps-key.pem ec2user@ipv4domain
- cd project directory
- source .venv/bin/activate
- uvicorn app:app --host 0.0.0.0 --port 8000
