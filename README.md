# IN-GPS (Server, AWS-ec2)

IN-GPS server 초기버전입니다. python으로 작성했으며 mqtt 프로토콜을 통하여 게이트웨이에 들어온 값을 DB에 저장하며 fast API를 통하여 Mobile로 데이터를 송신합니다.

## Project Structure
 - app.py
 - db.py
 - mqtt_subsriber.py
 - requirement.txt  

## Features
- DummyData 송신 후 로그확인
- DB검증
- EC2활용 체크


## Requirements
- Python: 3.x.x.x
- ingps-key.pem

## Build & Flash
- EC2를 사용하므로 본인말고는 열 수 없음(참고사항)
- powershell start
- ssh -i ingps-key.pem ec2user@ipv4domain
- cd project directory
- source .venv/bin/activate
- uvicorn app:app --host 0.0.0.0 --port 8000
*현재 systmd로 열려있음 *
