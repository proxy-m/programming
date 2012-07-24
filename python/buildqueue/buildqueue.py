#!/usr/bin/env python

# This is a small Python project to implement automatic continuous build for my projects.
# I currently have 3 types of builds: linux arm, linux x86 and windows x86. This tool
# needs to check the branches periodically to see if I have committed anywhere, and 
# automatically start a build.

import os
import sys
from datetime import datetime,date
import time
import threading
import pysvn
import Queue
import logging
import errno
import subprocess
import ConfigParser
from logging.handlers import RotatingFileHandler


## TODO
# -- add git repo support
# -- replace while true with decent condition
# -- fix nightly
# -- add notification mechanism

##################################################################################
class BuildQueue(Queue.PriorityQueue):
	''' Wrapper class for Queue to filter out double entries '''
	def __init__(self, queuelength, platform):
		Queue.PriorityQueue.__init__(self, queuelength)
		self.builds = {} # maintain a hash of branches added to sift out doubles
		self.lock = threading.Lock()
		self.platform = platform
	
	def enqueue(self, item):
		# if a build is in the queue then don't add it again
		self.lock.acquire()
		try:
			if(self.builds[item[2].name]):
				log.debug('Branch ' + item[2].name + ' is already in the ' + self.platform + ' queue - skipping')
		except KeyError:
			# else put it in the buildqueue
			self.put_nowait(item)
			self.builds[item[2].name] = True
		self.lock.release()

	def dequeue(self):
		item = self.get()
		self.lock.acquire()
		del self.builds[item[2].name]
		self.lock.release()
		return item

class Build:
	def __init__(self, name, path, lastauthor, buildtype = 'experimental'):
		self.name = name
		self.path = path
		self.lastauthor = lastauthor
		self.buildtype = buildtype

class ThreadClass(threading.Thread):
	def __init__(self, queue, name):
		threading.Thread.__init__(self)
		self.queue = queue
		self.name = name
		self.client = pysvn.Client()
		self.stop_event = threading.Event()

	def stop(self):
		self.stop_event.set()

	def run(self):
		self.client.callback_get_login = get_login
		log.debug("%s started at time: %s" % (self.name, datetime.now()))
		exportpath = os.path.normpath(os.path.expandvars(str(config.get('general','pivotdirectory')) + '/' + self.name + '/buildscripts'))

		try:
			os.makedirs(exportpath)
		except OSError, e:
			if e.errno == errno.EEXIST:
				pass
			else: raise

		while not self.stop_event.isSet():
			# returned value consists of: priority, sortorder, build object
			item = self.queue.dequeue()
			buildscript = exportpath + '/' + item[2].name + '-build-stage2.cmake'
			# export the buildscript that will perform the actual build of the branch
			try:
				self.client.export(item[2].path + '/' + str(config.get('general','buildscript')), buildscript, force=True, recurse=False)
			except pysvn.ClientError, e:
				log.debug("Failed to export the buildscript for " + item[2].name + ':' + str(e))
				self.queue.task_done()
				continue

			# run the buildscript
			try:
				command = "ctest"
				argument1 = "--script"
				argument2 = buildscript + ",platform=" + self.name + ";branch=" + item[2].name + ";repo=" + item[2].path.replace('svn://','') + ";repotype=svn" + ";server" + ";" + item[2].buildtype
				#log.debug("cmdline: " + command + ' ' + argument1 + argument2)
				retcode = subprocess.call([command, argument1, argument2])
				
				if retcode < 0:
					log.debug(self.name + " " + item[2].name + " was terminated by signal: " + str(-retcode))
					self.queue.task_done()
					continue	
				else:
					log.debug(self.name + " " + item[2].name + " returned: " + str(retcode))
					self.queue.task_done()
					continue
			except OSError, e:
				log.debug(self.name + " " + item[2].name + " execution failed: " + str(e))
				self.queue.task_done()
				continue	

			log.debug(self.name + " " + item[2].name + ': done build ' + item[2].name)
			self.queue.task_done()

##################################################################################
# callback needed for the subversion client
def get_login( realm, username, may_save ):
	"""callback implementation for Subversion login"""
	return True, config.get('subversion', 'user'), config.get('subversion', 'password'), True

def addToBuildQueues(build):
	for queue in BuildQueues[:]:
			try:
				# for now just using one priority. The second argument is used for sorting within a priority level
				queue.enqueue((1, 1, build))
			except Queue.Full:
					log.debug(queue.name + ' queue full, skipping: ' + build.name)

def addSubversionBuilds():
	client = pysvn.Client()
	client.callback_get_login = get_login
	svnRepository = str(config.get('subversion', 'repository'))

	# find branch names (returns a list of tuples)
	branchList = client.list(svnRepository + '/branches', depth=pysvn.depth.immediates)

	# skip the first entry in the list as it is /branches (the directory in the repo)
	for branch in branchList[1:]:
		log.debug('Found branch: ' +  os.path.basename(branch[0].repos_path) + ' created at revision ' + str(branch[0].created_rev.number))
		addToBuildQueues(Build(os.path.basename(branch[0].repos_path), svnRepository + branch[0].repos_path, branch[0].last_author))

	addToBuildQueues(Build('trunk', svnRepository + '/trunk', branch[0].last_author))

def addSubversionNightly():
	addToBuildQueues(Build('trunk', svnRepository + '/trunk', branch[0].last_author, 'nightly'))

def writeDefaultConfig():
	try:
		defaultConfig = open(os.path.expanduser('~/buildqueue.examplecfg'), 'w')
		defaultConfig.write('[general]\n')
		defaultConfig.write('pivotdirectory : \n')
		defaultConfig.write('buildscript : \n')
		defaultConfig.write('# loglevel may be one of: debug, info, warning, error, critical\n')
		defaultConfig.write('loglevel   : \n')
		defaultConfig.write('# for example @example.com\n')
		defaultConfig.write('maildomain : \n')
		defaultConfig.write('[subversion]\n')
		defaultConfig.write('repository : <repository url>\n')
		defaultConfig.write('user       : <username>\n')
		defaultConfig.write('password   : <password>\n')
		defaultConfig.write('[git]\n')
		defaultConfig.write('repository : <repository url>\n')
		defaultConfig.close()
		print 'Default configuration written as: ' + defaultConfig.name
	except IOError, e:
		print 'Failed to write default configuration: ' + str(e)

	sys.exit()

def main():
	global config
	config = ConfigParser.SafeConfigParser()
	configfiles = config.read(['/etc/buildqueue', os.path.expanduser('~/.buildqueue')])
	if (len(configfiles) == 0):
		print 'No config files found'
		writeDefaultConfig()
		sys.exit()

	try:
		config.get('general', 'buildscript')
		config.get('general', 'loglevel')
		config.get('general', 'maildomain')
		config.get('general', 'pivotdirectory')
		config.get('subversion', 'repository')
		config.get('subversion', 'user')
		config.get('subversion', 'password')
	except ConfigParser.Error, e:
		print str(e)
		writeDefaultConfig()

	# setup logging for both console and file
	numeric_level = getattr(logging, str(config.get('general', 'loglevel')).upper(), None)
	if not isinstance(numeric_level, int):
		    raise ValueError('Invalid log level: %s' % config.get('general', 'loglevel'))

	logging.basicConfig(format='%(levelname)s: %(message)s', level=numeric_level)
	fh  = logging.handlers.RotatingFileHandler('buildbot.log', maxBytes=1048576, backupCount=5)
	fh_fmt = logging.Formatter("%(levelname)s\t: %(message)s")
	fh.setFormatter(fh_fmt)

	global log
	log = logging.getLogger()
	log.addHandler(fh)
	log.debug('starting main loop')

	QueueLen    = 48 # just a stab at a sane queue length
	global BuildQueues
	BuildQueues = []

	if sys.platform[:5] == 'linux':
		BuildQueues.append(BuildQueue(QueueLen, 'linux-arm'))
		BuildQueues.append(BuildQueue(QueueLen, 'linux-x86'))
	elif sys.platform[:3] == 'win':
		BuildQueues.append(BuildQueue(QueueLen, 'windows-x86'))
	else:
		log.debug("Unknown platform, don't know which buildqueue to start")
		sys.exit()

	# Start build queue threads
	for queue in BuildQueues[:]:
		thread = ThreadClass(queue, queue.platform)
		# let threads be killed when main is killed
		thread.setDaemon(True)

		try:
			thread.start()
		except (KeyboardInterrupt, SystemExit):
			thread.stop()
			thread.join()
			sys.exit()

	lastNightlyTime = datetime(date.today().year, date.today().month, date.today().day, 1, 0, 0)

	while True:
		currentTime = datetime.now()
		delta = currentTime - lastNightlyTime

		if(delta.seconds > (24 * 3600)):
			lastNightlyTime = datetime(date.today().year, date.today().month, date.today().day, 1, 0, 0)
			addSubversionNightly()

		addSubversionBuilds()
		time.sleep(30)

##################################################################################
if __name__ == '__main__':
	main()
