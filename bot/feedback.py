import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import discord

from bot.config import config
import bot.db
from pony.orm import db_session, commit


class Feedback:
    def __init__(self):
        mailserver = smtplib.SMTP(config.smtp.host, 587)
        # identify ourselves to smtp gmail client
        mailserver.ehlo()
        # secure our email with tls encryption
        mailserver.starttls()
        # re-identify ourselves as an encrypted connection
        mailserver.ehlo()
        mailserver.login(config.smtp.email, config.smtp.password)
        self.mailserver = mailserver

    @db_session
    def send_feedback(
        self,
        member_id: int,
        member_nick: str,
        guild_id: int,
        guild_name: str,
        message: str,
    ):
        feedback = bot.db.Feedback(user_id=str(member_id), user_name=member_nick, guild=str(guild_id), message=message)
        commit()

        msg = MIMEMultipart()
        msg["From"] = "arm.localhost@gmail.com"
        msg["To"] = "arm.localhost@gmail.com"
        msg["Subject"] = f"WikiBot: Feedback from {member_nick} from {guild_name}"
        message = (
            f"Guild ID: {guild_id}, Guild Name: {guild_name}"
            + f"\nMember ID: {member_id}, Member Name: {member_nick}"
            + f"\nFeedback: {message}"
        )

        msg.attach(MIMEText(message))

        self.mailserver.sendmail(config.smtp.from_email, config.smtp.email, msg.as_string())

    def close(self):
        self.mailserver.quit()
