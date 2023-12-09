import smtplib
import os

from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import make_msgid
import logging
from dotenv import load_dotenv

def send(recipient, email, subject, message):
    
    # Create the base text message.
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = Address("Ivo de Geus", addr_spec=os.environ['SMTP_EMAIL'])
    msg['To'] = (Address(recipient, addr_spec=email))
    msg.set_content(f"""\
    Beste {recipient},

    Dit is een speciaal bericht vanuit de ZweefApp. 
    
    {message}

    -- Webmaster ZweefApp
    """)

    # Add the html version.  This converts the message into a multipart/alternative
    # container, with the original text message as the first part and the new html
    # message as the second part.
    asparagus_cid = make_msgid()
    msg.add_alternative(f"""\
    <html>
    <head></head>
    <body>
        <div style='max-width: 300px; margin: 0 auto; background: rgba(255,255,255,0.3); padding: 36px 24px; border-radius: 8px; border: 2px solid #eee;'>
        <img src="cid:{asparagus_cid[1:-1]}" style='display: block; height: 100px; width: 100px; margin: 0 auto 20px auto;' />
        <p>Beste {recipient},</p>
        <p>Dit is een speciaal bericht over jouw registraties in de ZweefApp. </p>
        <p>{message}</p>
        <p>-- Webmaster ZweefApp</p>
        </div>
    </body>
    </html>
    """, subtype='html')

    # Now add the related image to the html part.
    dirname = os.path.dirname(__file__)
    with open(os.path.join(dirname, "zweefvliegcentrum-35mm.gif"), 'rb') as img:
        msg.get_payload()[1].add_related(img.read(), 'image', 'jpeg', cid=asparagus_cid)

    # Send the message via local SMTP server.
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
       smtp_server.login(os.environ['SMTP_EMAIL'], os.environ['SMTP_PASS'])
       smtp_server.send_message(msg)
    print("Message sent!")
    
    
if __name__ == '__main__':
    
    # Load environment variables from .env file.
    load_dotenv()

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)
    
    send('Ivo de Geus', os.environ['SMTP_EMAIL'], 'Test door ZCNK', 'Testbericht.')