import importlib
import tempfile
import subprocess
import platform
import os

def ensurePip(silent = False):
	try:
		pip = importlib.import_module('pip')
		# If pip 10.x.x or greater is installed, the string test is insufficient
		if pip.__version__ < '9.0.1' and int(pip.__version__.split('.')[0]) <= 9:
			# Upgrade pip
			if platform.system() == 'Windows':
				args = ['python', '-m', 'pip']
			else:
				args = ['pip']
			args += ['install', '-U', 'pip']
			stdout = open(os.devnull, 'w') if silent else subprocess.STDOUT
			try:
				result = subprocess.call(args, stdout=stdout)
				if result != 0:
					print "'{0}' returned {1}. Pip may not have been upgraded successfully.".format(' '.join(args), result)
			except Exception, e:
				print "Exception upgrading pip. Pip may not have been upgraded successfully: \n{0}".format(e.message)
	except:
		# Install pip
		# Download get_pip.py from http://bootstrap.pypa.io/get-pip.py.
		import urllib2
		request = urllib2.urlopen("http://bootstrap.pypa.io/get-pip.py")
		get_pip = request.read()
		request.close()

		# It might be possible to run this in-place, using exec() for example, but write it out to a file and subprocess another python instance, for sanity's sake :)
		scriptFile = os.path.join(tempfile.gettempdir(), "get-pip.py")
		with open(scriptFile, 'wt') as f:
			f.write(get_pip)

		args = ['python', scriptFile]
		stdout = open(os.devnull, 'w') if silent else subprocess.STDOUT
		result = subprocess.call(args, stdout=stdout)
		if result != 0:
			print "get-pip.py returned {0}. Pip may not have been installed successfully.".format(result)
	
def ensurePackage(package, installPath = None, silent = False):
	try:
		importlib.import_module(package)
	except ImportError:
		ensurePip(silent)
		import pip
		pip.main(['install', installPath if installPath else package] + (['--quiet'] if silent else []))
	finally:
		# Reload the site module to make sure the site-packages area is on the path, 
		#  otherwise importing the freshly installed module may fail (if it's the first of its kind)
		#   https://stackoverflow.com/questions/25384922/how-to-refresh-sys-path
		import site
		reload(site)
		globals()[package] = importlib.import_module(package)
