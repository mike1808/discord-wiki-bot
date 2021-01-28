FROM python:3.9-buster

WORKDIR /bot

COPY requirements.txt ./

RUN pip install -r requirements.txt

# Bundle app source
COPY . /bot

WORKDIR /

CMD [ "python", "-m", "bot.bot" ]
