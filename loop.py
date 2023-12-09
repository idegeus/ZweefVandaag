# =============================================
# Background member management for ZweefApp.
# =============================================
import os
import random
import requests
import json
from dotenv import load_dotenv
import logging
from datetime import datetime as dt
from datetime import timedelta
from collections import defaultdict
from util.send_email import send
from ics import Calendar
import os
import time
import locale

class ZweefApp:
    
    config = {
        'config_user_id': 121,
        'production': True,
        'auth': {
            'user_token': False,
            'api_token': False,
            'supersaas_pax_token': False
        },
        'eps': {
            'int': 'https://admin.zweef.app/club/zcnk/internal_api/',
            'ext': 'https://admin.zweef.app/club/zcnk/'
        }
    }
    
    # Cache from ZweefApp
    days = []
    accounts = []
    id2user = {}
    dayid2messageid = {}
    SS_pax_registrations = False
    
    def __init__(self, production=False):
        """Initialises main object."""
        self.initialise_config()
        self.load_members()
        
        # Makes requests slower, to not occupy the main servers so much.
        self.config['production'] = production
        
        # Set the locale to Dutch
        locale.setlocale(locale.LC_TIME, 'nl_NL')

        logging.debug('Initialised ZweefApp object.')
    
    def _user_headers(self):
        """Generates headers for admin-user requests to internal API's.

        Returns:
            dict: Header dictionary for use in requests.
        """
        
        headers = {
            'Referer': 'https://zcnk.zweef.app/',
            'Version': '3.0.22',
            'Content-Type': 'application/json',
            'Origin': 'https://zcnk.zweef.app',
        }
        
        if self.config['auth']['user_token']:
           headers['Authorization'] = f"Bearer {self.config['auth']['user_token']}"
           
        return headers
        
    def _api_headers(self):
        """Generates headers for admin-user requests to external API's.

        Returns:
            dict: Header dictionary for use in requests.
        """
        
        headers = {
            'Referer': 'https://zcnk.zweef.app/',
            'Content-Type': 'application/json',
            'Origin': 'https://zcnk.zweef.app',
        }
        
        if self.config['auth']['api_token']:
            headers['X-API-KEY'] = f"{self.config['auth']['api_token']}"
           
        return headers
    
    def initialise_config(self):
        
        # Load environment variables from .env file.
        load_dotenv()
        
        # Read API token from .env file
        self.config['auth']['api_token'] = os.environ['AUTH_API_KEY']
        self.config['auth']['supersaas_pax_token'] = os.environ['SUPERSAAS_PAX_API_KEY']

        # Do a user-login procedure and store token in config dict.
        json_data = {
            'grant_type': 'login',
            'client_secret': os.environ['AUTH_ADMIN_SECRET'],
            'email': os.environ['AUTH_ADMIN_EMAIL'],
            'password': os.environ['AUTH_ADMIN_PASS'],
        }
        response = requests.post(self.config['eps']['int'] + 'auth/login.json', headers=self._user_headers(), json=json_data)
        self.config['auth']['user_token'] = response.json()['access_token']
        
    def refresh(self):
        """Main entrypoint for refreshing webservices and information points."""
        self.process_flying_days()
        
    def load_members(self):
        """
        Gets information about members and instructors. 
        - INTERNAL API
        """
        
        result = requests.get(self.config['eps']['ext'] + "api/accounts.json", headers=self._api_headers())
        result = result.json()
        self.accounts = result
        self.id2user = {x['id']:x for x in result}
        
    def process_flying_days(self):
        """
        Gets information about flying days.
        - INTERNAL API
        """
        
        # Get days in ZweefApp.
        result = requests.get(self.config['eps']['int'] + "days.json", headers=self._user_headers())
        result = result.json()
        
        # Filter out 1) non-flying days, 2) days in the past, 3) days more than 14 days in the future
        days = [day for day in result['days'] if day['is_vliegend'] == True]
        
        yesterday = dt.now() - timedelta(days=1)
        days = [day for day in days if dt.strptime(day['datum'], "%Y-%m-%d") > yesterday]
        
        # future = dt.now() + timedelta(days=14)
        # days = [day for day in days if dt.strptime(day['datum'], "%Y-%m-%d") < future]
        
        days = sorted(days, key=lambda day: dt.strptime(day['datum'], "%Y-%m-%d"))
        
        # Clean and cache signups for calculation on weekly basis in next part.
        signups_cache = {}
        signups_student_week = defaultdict(lambda: defaultdict(list))
        for day in days:
            
            if self.config['production']:
                time.sleep(1)
            
            # Parse datum
            day['datum'] = dt.strptime(day['datum'], "%Y-%m-%d")

            # Request information about day
            response = requests.post(
                self.config['eps']['int'] + 'aanmeldingen/get_dag.json',
                headers=self._user_headers(),
                json={'dag_id': day['dag_id']},
            ).json()
            
            # Cache all existing messages placed by bot.
            botmsgs = [message for message in response['messages'] if 'Vandaag:' in message['message']]
            if len(botmsgs) > 0:
                self.dayid2messageid[day['dag_id']] = botmsgs[0]['id']
            
            # Do some basic filtering (not every signup is aangemeld, some have signed off again.)
            valid_signups = [signup for signup in response['aanmeldingen'] if signup['aangemeld'] == True]
            for i, signup in enumerate(valid_signups):
                valid_signups[i]['date_aangemeld'] = dt.fromisoformat(signup['date_aangemeld'])
            
            # Write to cache to continue.
            signups_cache[day['dag_id']] = valid_signups
        
            # Students are DBO'ers only.
            students = [signup for signup in valid_signups
                        if 'solist' in signup['vlieger']['group_names']]
            
            # Calculate total student slots registered per week, to better spread out load.
            for signup in students:
                signups_student_week[signup['vlieger']['id']][day['datum'].isocalendar().week].append(signup['date_aangemeld'])
        
        
        # Now actually do the calculation.
        for day in days:
            
            valid_signups = signups_cache[day['dag_id']]
            day_date = day['datum']
            
            # Students are DBO'ers only. Signup moment is important for giving importance.
            students = [signup for signup in valid_signups if 'solist' in signup['vlieger']['group_names']]
            students = sorted(students, key=lambda signup: signup['date_aangemeld'])
            
            # Instructors are people (!) that have said to instruct explicitly.
            instr_ok = [signup for signup in valid_signups 
                        if signup['as_instructeur'] == True]
            
            # Instructors can sometimes be Out of Service if they have not indicated it.
            instr_nok = [signup for signup in valid_signups 
                         if signup['as_instructeur'] == False and 'instructeur' in signup['vlieger']['group_names']]
            
            # Output the results of our calculations. 
            logging.debug(f"up{len(valid_signups)} dbo{len(students)}, instr_ok{len(instr_ok)}/nok{len(instr_nok)}.")
            
            # ========================
            # DBO Student Logic Part
            # ========================
            day_DBO_signup_before = day_date - timedelta(days=7)
            day_isweekend = day_date.isoweekday() in [6, 7]
            day_DBO_signup_thu18h = day_date - timedelta(days=1, hours=6)
            if day_date.isoweekday == 7: # Correct for sunday
                day_DBO_signup_thu18h =- timedelta(days=1)
                
            # Logic for signing up students on time and per instructor. 
            students_processed = 0
            students_accepted = 0
            for signup in students:
                students_processed += 1
                
                args = {
                    'day_id': day['dag_id'], 
                    'day_date': day_date, 
                    'lid_id': signup['vlieger']['id'],
                }
                
                # If no instructor(s) assigned: notify.
                if len(instr_ok) == 0:
                    self._remove_aanmelding(reason='IST_UNAVAIL', **args)
                    continue
                
                # Limit signup to one week in advance.
                if signup['date_aangemeld'] < day_DBO_signup_before:
                    self._remove_aanmelding(reason='DBO_EARLY', **args)
                    continue
                
                # Limit signups per instructor.
                if students_processed > len(instr_ok) * 4:
                    self._remove_aanmelding(reason='DBO_FULL', **args)
                    continue
                
                # If date is not a weekday and there are multiple sign-ups, remove the non-first one. 
                signups_this_week = signups_student_week[signup['vlieger']['id']][day['datum'].isocalendar().week]
                priority_signup = min(signups_this_week)
                if (day_isweekend 
                    and signup['date_aangemeld'] < day_DBO_signup_thu18h 
                    and len(signups_this_week) > 1 
                    and signup['date_aangemeld'] is not priority_signup):
                    self._remove_aanmelding(reason='DBO_WEEKND_QUOTA', **args)
                    continue
                
                students_accepted += 1
    
            # ========================
            # PAX and Dagbericht Part
            # ========================
            pax = self.get_saas_pax(day_date)
            # logging.info(pax)
            n_pax = sum([len(slot['bookings']) for slot in pax['slots']])
            self.set_day_message(day['dag_id'], day_date, f'{n_pax} pax, zie nu.zweef.nl ({random.randint(1000, 9999)}).')
            
    def _remove_aanmelding(self, day_id, day_date, lid_id, reason):
        
        # Validate reasons.
        reasons = ["IST_UNAVAIL", "DBO_EARLY", "DBO_FULL", "DBO_WEEKND_QUOTA"]
        assert reason in reasons
        logging.debug(f'Removing {lid_id} from day {day_id} because of {reason}.')
        
        # Remove person from the list
        json_data = {
            'action': 'meld_af',
            'dag_id': day_id,
            'lid': lid_id,
        }
        requests.post(self.config['eps']['int'] + "aanmeldingen/save.json", 
                     headers=self._user_headers(),
                     json=json_data)
        
        # Compose message to the user.
        datum = dt.strftime(day_date, "%A %-d %B")
        lid = self.id2user[lid_id]
        lid_name =  f"{lid['first_name']} {lid['last_name']}"
        lid_email = lid['email']
        msgs = {
            "IST_UNAVAIL": f"Er hebben zich (nog) geen instructeurs ingeschreven op {datum}, dus kan je niet vliegen als DBO'er. De HoT is op de hoogte gesteld, probeer het later opnieuw.",
            "DBO_EARLY": f"Je mag vanaf 7 dagen voor {datum} aanmelden als DBO'er. Deze eis is er om te voorkomen dat te lang vooruit geboekt wordt.", 
            "DBO_FULL": f"De vliegdag op {datum} zit aan het maximale aantal DBO'ers (meestal 4 per instructeur). Deze eis is er om te voorkomen dat er te veel aspiranten zijn per instructeur.", 
            "DBO_WEEKND_QUOTA": f"Je hebt al eerder een andere dag deze week geboekt. Wissel de dag op {datum} om, probeer door de weeks te boeken, of probeer na donderdag 18:00 opnieuw: dan komen de open slots in het weekend beschikbaar."
        }
        
        # Send message.
        send(lid_name, lid_email, f"Over jouw afmelding in ZweefApp ZCNK op {datum}", msgs[reason])

    def set_day_message(self, day_id, day_date, message):
        id = self.dayid2messageid.get(day_id, None)
        prefix = 'Vandaag'
        json_data = {
            'message': f"{prefix} {message}",
            'as_email': False,
            'id': id,
            'dag_id': day_id,
            'datum': dt.strftime(day_date, "%Y-%m-%d"),
        }
        
        logging.debug(json_data)

        kek = requests.post(self.config['eps']['int'] + 'aanmeldingen/message.json',
            headers=self._user_headers(),
            json=json_data,
        )
        
        print(kek)
        print(kek.text)

    def get_saas_pax(self, day_date):

        # Request items for calendar 74955, which is the PAX calendar.
        url = "https://www.supersaas.com/api/range/74955.json"
        params = {
            'api_key': self.config['auth']['supersaas_pax_token'],
            'slot': True,
            'from': dt.strftime(day_date, "%Y-%m-%d"),
            'to': dt.strftime((day_date+timedelta(days=1)), "%Y-%m-%d"),
        }
        response = requests.get(url, params=params).json()
        return response

if __name__ == '__main__':
    
    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)
    
    zweef = ZweefApp(production=False)
    zweef.refresh()
    