from flask import Flask, abort, request, send_file, jsonify
from flask_cors import CORS
from flask_apscheduler import APScheduler
from flask_script import Manager
 
import time
from datetime import datetime
import json
import pymongo
from pymongo import MongoClient
import os
from email.message import EmailMessage
import smtplib
import subprocess
import sys
import glob

app = Flask(__name__)
manager = Manager(app)
cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

#Mongo Connection
client = MongoClient('mongodb://bed-600-312:27017/')
db = client.APR
job_col = db["Queued Releases"]


#Load scheduled jobs that were queued at server crash or shutdown (Fault-Tolerance)
@app.before_first_request
def load_scheduled_tasks():
	jobs = job_col.find()
	
	for j in jobs:
		app.apscheduler.add_job(func=scheduled_task, trigger='date', run_date=j['run_time'], args=[j['arguments']], id=str(j['request']))


'''
@app.before_request
def limit_remote_addr():
    print(request)
'''

def email_release_team(request_number,message):
	frm = "ProductionRelease@EatonVance.Com"
	to = "ReleaseEngineering@EatonVance.Com"
	Subj = f"{request_number} was approved after requested release date/time"
	
	msg = EmailMessage()
	msg.set_content(f"{message}")
	smtpserver = smtplib.SMTP("smtp.eatonvance.com")

	msg['Subject'] = Subj
	msg['From'] = frm
	msg['To'] = to    
	                  
	smtpserver.send_message(msg)
	smtpserver.quit()
	return

def get_release_type(path):
	types = []
	with open(path) as f:
		type_list = f.readline().rstrip().split(';')
		for t in type_list:
			if t != "" and t.startswith(('CIS','EDM','MarkitEDM','Database','Custom','Ab Initio','ESP')):
				types.append(t)
	return types

def get_release_status(path):
	fileHandle = open (path,"r")
	lineList = fileHandle.readlines()
	fileHandle.close()
	if(len(lineList) == 0):
		return "Error (Check Server)"
	last_line = lineList[len(lineList)-1]
	if(last_line.rstrip().endswith("SUCCESS")):
		return "Success"
	elif(last_line.rstrip().endswith("FAILURE")):
		return "Failure"
	elif(last_line.rstrip().endswith("CANCELLED")):
		return "Cancelled"
	elif("Job Scheduled for" in last_line.rstrip()):
		return "Scheduled"
	else:
		return "Running"


#Get configuration values for a specific application
@app.route('/get_config_attr')
def get_config_attr():
	app = request.args.get('app')
	attr = []

	with open('D:\\AutomationScripts\\Production Releases\\config.json') as config_file:
		configuration = json.load(config_file)

	if(app in configuration):
		return json.dumps(configuration[app])
	else:
		for key, value in configuration.items():
			if(app in configuration[key]):
				return configuration[key][app]

	return None

#Get all application names from config
@app.route('/get_config_opt')
def get_config_opt():
	options = []

	with open('D:\\AutomationScripts\\Production Releases\\config.json') as config_file:
		configuration = json.load(config_file)

	return json.dumps(configuration)

#Create new configuration entry
@app.route('/new_config', methods = ['POST'])
def new_config():
	if request.method == 'POST':
		try:
			data = json.loads(request.data)

			with open('D:\\AutomationScripts\\Production Releases\\config.json', 'r+') as config_file:
				configuration = json.load(config_file)

				app = data["App_Name"]

				attrs = list(data.items())[3:len(data.items())]


				if(app in configuration):
					raise ValueError("Already Exists")
				else:
					for key, value in configuration.items():
						if(app in configuration[key]):
							raise ValueError("Already Exists")

				configuration['Custom_App_Lookup'][app] = {}
							
				for key, value in attrs:
					configuration['Custom_App_Lookup'][app][key] = value
					

				config_file.seek(0)
				json.dump(configuration, config_file, indent=4, sort_keys=True)
				config_file.truncate()
		except ValueError as e:
			return jsonify(isError= True,message= f"{app} already has a configuration", statusCode= 501, data= str(e)), 501



	return jsonify(isError= False,message= f"Configuration for {app} has been created", statusCode= 200, data="Success"), 200

@app.route('/set_config', methods = ['POST'])
def set_config():
	if request.method == 'POST':
		try:
			data = json.loads(request.data)

			with open('D:\\AutomationScripts\\Production Releases\\config.json', 'r+') as config_file:
				configuration = json.load(config_file)

				app = data["App_Name"]

				if(app in configuration):
					for val in configuration[app]:
						configuration[app][val] = data[val] 
				else:
					for key, value in configuration.items():
						if(app in configuration[key]):
							for val in configuration[key][app]:
								configuration[key][app][val] = data[val] 

				config_file.seek(0)
				json.dump(configuration, config_file, indent=4, sort_keys=True)
				config_file.truncate()
		except Exception as e:
			return jsonify(isError= True,message= str(e), statusCode= 501, data= str(e)), 501


	return jsonify(isError= False,message= "Success", statusCode= 200, data="Success"), 200

@app.route('/get_requests')
def get_requests():
	searchString = request.args.get('searchString')

	files = []
	# r=root, d=directories, f = files
	for r, d, f in os.walk("D:/AutomationScripts/Production Releases/logs"):
		for file in f:
			if '.log' in file and searchString in file:
				files.append(r + "/" + file)

	data = {}
	for path in files:
		data[os.path.basename(os.path.splitext(path)[0])] = {"Location": path, "Created_Date": os.path.getmtime(path), "Release_Type": get_release_type(path), "Release_Status": get_release_status(path)}
	d = dict(sorted(data.items(), key=lambda items: items[1]['Created_Date'], reverse=True))
	#return json.dumps(sorted(data.items(), key=lambda items: items[1]['Created_Date']))[1:-1]
	return json.dumps(d)

@app.route('/log_data')
def log_data():
	path = request.args.get('path')
	with open(path,'r',newline='') as f:
		return json.dumps(f.readlines()[1:])

@app.route('/download')
def downloadFile():
	path = request.args.get('path')
	return send_file(path,as_attachment=True,attachment_filename="stdOut.txt")

@app.route('/scheduled-tasks', methods = ['GET','POST','DELETE'])
def scheduled_tasks():
	log_path = "D:\\AutomationScripts\\Production Releases\\logs\\"
	if request.method == 'GET':
		jobs_dict = {}
		try:
			jobs = app.apscheduler.get_jobs()
			for idx, j in enumerate(jobs):
				jb = j.__getstate__()
				jobs_dict[jb['id']] = {"Parameters": get_release_type(log_path+jb['id']+".log"), "Trigger Date": str(jb['next_run_time'])}
			return json.dumps(jobs_dict)
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			return jsonify(isError= True,message= "Failure", statusCode= 500,data=(f"{str(e)}{str(exc_tb.tb_lineno)}")), 500

	elif request.method == 'POST':
		try:
			data = json.loads(request.data)
			if(int((datetime.strptime(data['run_time'], '%m/%d/%Y %I:%M:%S %p').timestamp())) < datetime.now().timestamp()):
				email_release_team(data['id'],f"{data['id']} was requested to be deployed at {data['run_time']}, {log_path+data['id']+'.log'} but the ticket wasn't approved in time to deploy accordingly.")
				return jsonify(isError= True,message= "Failure", statusCode= 501,data=(f"Can not schedule job in the past!{data['run_time']}  {datetime.now().timestamp()}")), 501
			app.apscheduler.add_job(func=scheduled_task, trigger='date', run_date=data['run_time'], args=[data['arguments']], id=str(data['id']))
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			return jsonify(isError= True,message= "Failure", statusCode= 500,data=(f"{str(e)}{str(exc_tb.tb_lineno)}")), 500

		try:	
			job_col.insert_one({"request": data['id'],"arguments": data['arguments'],"run_time": data['run_time']})
		except Exception as e:
			logging.info(e)
	
		return f"{data['id']} to run at {data['run_time']}", 200

	elif request.method == 'DELETE':
		try:
			data = json.loads(request.data)
			app.apscheduler.remove_job(data['job_id'])
			os.remove(f"D:\\AutomationScripts\\Production Releases\\logs\\{data['job_id']}.log")
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			return jsonify(isError= True,message= "Failure", statusCode= 500,data=(f"{str(e)}{str(exc_tb.tb_lineno)}")), 500

		try:	
			job_col.delete_one({"request": data['job_id']})
		except Exception as e:
			logging.info(e)

		return f"{data['job_id']} has been cancelled", 200

def scheduled_task(args):
	output = subprocess.check_output(f"python \"D:\\AutomationScripts\\Production Releases\\Production_Release_Green.py\" {args}")
	return output

if __name__ == '__main__':
	app.run(host="0.0.0.0")
