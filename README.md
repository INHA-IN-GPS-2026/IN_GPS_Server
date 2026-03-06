# IN-GPS (Server, AWS-ec2)

IN-GPS server 초기버전입니다. python으로 작성했으며 API를 통하여 Mobile로 보낼 예정입니다.

## Project Structure
 - app.py
 - db.py
 - requirement.txt
 - 
## Features
- DummyData 송신 후 로그확인
- DB검증
- EC2활용 체크


## Requirements
- Python: 3.x.x.x
- AWS-EC2(서버 필요 시 이예찬에게 말씀해주시면 열어드리겠습니다.)

## Build & Flash
- EC2를 사용하므로 본인말고는 열 수 없음(참고사항)
- powershell start
- ssh -i ingps-key.pem ec2user@ipv4domain
- cd project directory
- source .venv/bin/activate
- uvicorn app:app --host 0.0.0.0 --port 8000
