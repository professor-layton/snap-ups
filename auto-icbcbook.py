import re
import sys
import time
import json
import queue
import requests
from loguru import logger
from bs4 import BeautifulSoup
from dateutil.tz import tzlocal
from flask import Flask, request
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter, Retry
from apscheduler.schedulers.background import BackgroundScheduler
# 'flask_ngrok' is so obsolete and probably can't work with latest ngrok binary, so use ngrok directly
# from flask_ngrok import run_with_ngrok

# user parameters
DRVR_LASTNAME = "<Your Last Name>"
LICENSE_TYPE = "5-R-1"
LICENSE_NUMBER = "<Your License>"
SIGNIN_KEYWORD = "<Your Password>"
START_DATE = "2024-10-09" # format: YYYY-MM-DD
CLOSE_DATE = "2024-10-19"
START_TIME = "09:30" # format: HH:mm
CLOSE_TIME = "15:30"
ORIGIN_EMAIL = "chensiyu1618@gmail.com"
CHANGE_EMAIL = "ad3b7c43d6f9fe7bac44@cloudmailin.net"
OFFICE_REGEX = "Langley.*Willowbrook" # "Campbell.*"

# there're 2 types of timeout in requests
# 1. connect timeout
# 2. reading timeout (if it's not set and network is slow, app'll be in waiting state until reading completed, looks like being hanged) 
CONN_TIMEOUT = 8
READ_TIMEOUT = 16
MAXI_RETRIES = 4

FLASK_EXEC_PORT = 5000
MAX_RETRY_TIMES = 2
MAX_WAIT_SECONDS = 16
SCHED_JOB_ID = "apiTrigger"
SCHED_INTERVAL = 40
STARTUP_DELAY_SECS = 4
RESCHED_DELAY_SECS = 8
AGENT_HEADER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

queueOTP = queue.Queue()
logger.remove()
logger.add(sys.stderr, format="{time:YYYY-MM-DD HH:mm:ss} {level} {message}", level="INFO")

# global session for setting of 'Retry'
session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=Retry(total=MAXI_RETRIES, backoff_factor=1.0))) # 0.0s, 2.0s, 4.0s, 8.0s, ...

app = Flask(__name__)
@app.route("/interceptOTP", methods = ['GET', 'POST'])
def intercept():
    parsedHtml = BeautifulSoup(request.get_json()['html'], 'html.parser')
    codeOTP = parsedHtml.body.find('h2').text
    logger.info("intercept OTP from email, %s" % str(codeOTP))
    queueOTP.put(codeOTP)
    return "intercept done"

def current_timestamp():
    timestamp = time.localtime()
    timestampStr = time.strftime("%Y-%m-%dT%H:%M:%S", timestamp) # 2024-09-25T18:29:39
    return timestampStr

def update_emailaddress(token, drvrId, firstName, email, phoneNum):
    updateEmailRequestHeader = {"Authorization": token, "User-Agent": AGENT_HEADER}
    updateEmailRequestBody = {
        "drvrId": drvrId,
        "email": email, 
        "firstName": firstName,
        "lastName": DRVR_LASTNAME,
        "licenseNumber": LICENSE_NUMBER,
        "optInFlags": {
            "email": "Y",
            "sms": "Y"
        },
        "phoneNum": phoneNum
    }
    updateEmailResponse = session.request('PUT','https://onlinebusiness.icbc.com/deas-api/v1/web/updateContactDetails', headers=updateEmailRequestHeader, json=updateEmailRequestBody, timeout=(CONN_TIMEOUT,READ_TIMEOUT))
    if 200 != updateEmailResponse.status_code:
        return False
    return True

def token_driver_info():
    loginReqestHeader = {"User-Agent": AGENT_HEADER}
    loginReqestBody = {"drvrLastName": DRVR_LASTNAME, "licenceNumber": LICENSE_NUMBER, "keyword": SIGNIN_KEYWORD}
    loginResponse = session.request('PUT','https://onlinebusiness.icbc.com/deas-api/v1/webLogin/webLogin', headers=loginReqestHeader, json=loginReqestBody, timeout=(CONN_TIMEOUT,READ_TIMEOUT))
    return loginResponse.status_code, loginResponse.headers['Authorization'], json.loads(loginResponse.text)

def icbc_apicall_trigger(sched, queueOTP):
    logger.info("API call process start to trigger")
    # extract access token
    statusCode, accessToken, drvrInfo = token_driver_info()
    if 200 != statusCode:
        logger.error("Failed to login and retrieve access token[exit], %s" % str(statusCode))
        sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
        return
    logger.debug(accessToken)
    drvrId = drvrInfo['drvrId']
    firstName = drvrInfo['firstName']
    phoneNum = drvrInfo['phoneNum']

    # find office position ID
    getOfficeReqestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
    getOfficeReqestBody = {"examType": LICENSE_TYPE, "startDate": START_DATE}
    getOfficeResponse = session.request('PUT','https://onlinebusiness.icbc.com/deas-api/v1/web/getPosByExam', headers=getOfficeReqestHeader, json=getOfficeReqestBody, timeout=(CONN_TIMEOUT,READ_TIMEOUT))
    if 200 != getOfficeResponse.status_code:
        logger.error("Failed to acquire road test office list[exit], %s" % str(getOfficeResponse.status_code))
        sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
        return
    getOfficeResponseJson = json.loads(getOfficeResponse.text)
    pattern = re.compile(OFFICE_REGEX)
    matchFlag = False;
    officePosId = -1;
    for office in getOfficeResponseJson:
        result = pattern.search(office['agency'])
        if None == result:
            continue;
        elif len(result.groups()) > 1:
            logger.error("Failed to match office regex[exit], %s" % str(result.group()))
            sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
            return
        matchFlag = True
        officePosId = office['posId']
        break;
    if not matchFlag:
        logger.error("No road test office found[exit], %s" % OFFICE_REGEX)
        sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
        return

    # get available appointments
    getAppointmentsRequestBody = {
        "aPosID": officePosId,
        "examType": LICENSE_TYPE,
        "examDate": START_DATE,
        "ignoreReserveTime": False,
        "prfDaysOfWeek":"[0,1,2,3,4,5,6]",
        "prfPartsOfDay":"[0,1]",
        "lastName": DRVR_LASTNAME,
        "licenseNumber": LICENSE_NUMBER
    }
    getAppointmentsRequestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
    getAppointmentsResponse = session.request('POST','https://onlinebusiness.icbc.com/deas-api/v1/web/getAvailableAppointments', headers=getAppointmentsRequestHeader, json=getAppointmentsRequestBody, timeout=(CONN_TIMEOUT,READ_TIMEOUT))
    if 200 != getAppointmentsResponse.status_code:
        logger.error("Failed to acquire available appointments of given office[exit], %s" % str(getAppointmentsResponse.status_code))
        sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
        return
    getAppointmentsResponseJson = json.loads(getAppointmentsResponse.text)
    if 0 == len(getAppointmentsResponseJson):
        logger.info("No appointment available[exit], %s" % START_DATE)
        return
    # pick up the first one if more than one timeslot meet given criteria
    selectedAppointment = None
    for iterateAppointment in getAppointmentsResponseJson:
        appointmentDate = iterateAppointment['appointmentDt']['date']
        appointmentTime = iterateAppointment['startTm']
        if appointmentDate >= START_DATE and appointmentDate <= CLOSE_DATE and appointmentTime >= START_TIME and appointmentTime <= CLOSE_TIME:
            selectedAppointment = iterateAppointment
            break
        else:
            logger.info("Get one appointment but does not meet criteria, '{0}','{1}'".format(appointmentDate, appointmentTime))
    if None == selectedAppointment:
        logger.info("No appointment meets criteria[exit], '{0}','{1}','{2}','{3}'".format(START_DATE, CLOSE_DATE, START_TIME, CLOSE_TIME))
        return
    logger.info("Get one desired appointment, '{0}','{1}'".format(selectedAppointment['appointmentDt']['date'], selectedAppointment['startTm']))

    # lock appointment
    lockAppointmentsRequestBody = {
        "appointmentDt": {
            "date": selectedAppointment['appointmentDt']['date'],
            "dayOfWeek": selectedAppointment['appointmentDt']['dayOfWeek']
        },
        "dlExam": {
            "code": LICENSE_TYPE,
            "description": LICENSE_TYPE + "-ROAD"
        },
        "drvrDriver": {
            "drvrId": drvrId
        },
        "drscDrvSchl": {},
        "instructorDlNum": None,
        "bookedTs": current_timestamp(),
        "startTm": selectedAppointment['startTm'],
        "endTm": selectedAppointment['endTm'],
        "posId": officePosId,
        "resourceId": selectedAppointment['resourceId'],
        "signature": selectedAppointment['signature']
    }
    lockAppointmentRequestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
    lockAppointmentResponse = session.request('PUT','https://onlinebusiness.icbc.com/deas-api/v1/web/lock', headers=lockAppointmentRequestHeader, json=lockAppointmentsRequestBody, timeout=(CONN_TIMEOUT,READ_TIMEOUT))
    if 200 != lockAppointmentResponse.status_code:
        logger.error("Failed to lock one available appointment of given office[exit], %s" % str(lockAppointmentResponse.status_code))
        sched.modify_job(job_id=SCHED_JOB_ID, next_run_time=datetime.now(tzlocal()) + timedelta(seconds=RESCHED_DELAY_SECS))
        return
    lockAppointmentResponseJson = json.loads(lockAppointmentResponse.text)

    maxRetryTimes = MAX_RETRY_TIMES
    maxWaitSeconds = MAX_WAIT_SECONDS
    while maxRetryTimes > 0:
        # send OTP
        sendOTPRequestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
        sendOTPRequestBody = {"bookedTs": current_timestamp(), "drvrID": drvrId, "method": "E"}
        # if appointment get locked, we'd better have retries and more time for timeout in case of losing the chance easily
        sendOTPResponse = session.request('POST', 'https://onlinebusiness.icbc.com/deas-api/v1/web/sendOTP', headers=sendOTPRequestHeader, json=sendOTPRequestBody, timeout=(2*CONN_TIMEOUT,2*READ_TIMEOUT))
        if 200 != sendOTPResponse.status_code:
            logger.error("Failed to send OTP to phone number in setting, %s" % str(sendOTPResponse.status_code))
            # continue to wait MAX_WAIT_SECONDS seconds if sending OTP failed in case too short interval of sending request
        else:
            sendOTPResponseJson = json.loads(sendOTPResponse.text)
        while maxWaitSeconds > 0:
            time.sleep(1)
            # check if received OTP from the queueOTP
            if queueOTP.empty():
                continue
            codeOTP = queueOTP.get()
            # verify OTP
            verifyOTPRequestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
            verifyOTPRequestBody = {"bookedTs": current_timestamp(), "drvrID": drvrId, "code": codeOTP}
            verifyOTPResponse = session.request('PUT', 'https://onlinebusiness.icbc.com/deas-api/v1/web/verifyOTP', headers=verifyOTPRequestHeader, json=verifyOTPRequestBody, timeout=(2*CONN_TIMEOUT,2*READ_TIMEOUT))
            if 200 != verifyOTPResponse.status_code:
                logger.error("Failed to verify OTP, %s" % str(verifyOTPResponse.status_code))
            else:
                verifyOTPResponseJson = json.loads(verifyOTPResponse.text)
                if "VERIFIED" == verifyOTPResponseJson['status']:
                    logger.info("Given OTP verified successfully")
                    # try to resume driver's email address before request final API for booking
                    if not update_emailaddress(accessToken, drvrId, firstName, ORIGIN_EMAIL, phoneNum):
                        logger.warn("Failed to restore driver's email address to %s" % ORIGIN_EMAIL)
                    else:
                        time.sleep(2)
                    bookRequestHeader = {"Authorization": accessToken, "User-Agent": AGENT_HEADER}
                    bookRequestBody = {"userId": "WEBD:" + str(drvrId), "appointment":{"drvrDriver":{"drvrId": drvrId}}}
                    bookResponse = session.request('PUT', 'https://onlinebusiness.icbc.com/deas-api/v1/web/book', headers=bookRequestHeader, json=bookRequestBody, timeout=(2*CONN_TIMEOUT,2*READ_TIMEOUT))
                    if 200 != bookResponse.status_code:
                        logger.error("Failed to request final book, %s" % str(bookResponse.status_code))
                    else:
                        logger.info("Request final book successfully")
                        sched.remove_job(SCHED_JOB_ID)
                        logger.info("Whole process done, icbc_apicall_trigger exit soon")
                        return
                else: # status = 'ATTEMPTED'
                    logger.error("Given OTP is NOT correct, %s" % str(codeOTP))
            maxWaitSeconds = maxWaitSeconds - 1
        maxRetryTimes = maxRetryTimes - 1
        maxWaitSeconds = MAX_WAIT_SECONDS
    logger.info("SHOULD NEVER REACH HERE")

if __name__ == "__main__":
    # change driver's email if needed
    statusCode, accessToken, drvrInfo = token_driver_info()
    if 200 != statusCode:
        logger.error("Failed to login and retrieve access token, %s" % str(statusCode))
        exit(0)
    email = drvrInfo['email']
    drvrId = drvrInfo['drvrId']
    firstName = drvrInfo['firstName']
    phoneNum = drvrInfo['phoneNum']
    if email != CHANGE_EMAIL:
        if not update_emailaddress(accessToken, drvrId, firstName, CHANGE_EMAIL, phoneNum):
            logger.error("Failed to change driver's email to %s" % CHANGE_EMAIL)
            exit(0)
    else:
        logger.info("No need to change driver's email")
    # create none-blocking apscheduler
    sched = BackgroundScheduler(timezone='MST')
    jobAdded = sched.add_job(icbc_apicall_trigger, 'interval', id=SCHED_JOB_ID, \
        seconds=SCHED_INTERVAL, max_instances=1, args=(sched, queueOTP), \
        next_run_time=datetime.now(tzlocal()) + timedelta(seconds=STARTUP_DELAY_SECS))
    sched.start()
    app.run(port=FLASK_EXEC_PORT, debug=False) # run on default port '5000'
