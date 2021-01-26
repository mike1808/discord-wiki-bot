FROM python:3.9-slim-buster

WORKDIR /bot

COPY requirements.txt ./

RUN pip install -r requirements.txt

# Bundle app source
COPY . /bot

CMD [ "python", "bot.py" ]
