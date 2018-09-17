'''
Created on 2 Oct 2014
@author: mbailey
'''

import subprocess
import sys
import os

ShowWarningDialogByDefault = False

ui_logging_function = None

def logging_function(message, waitForUserInput=False, offerAbort=False):
	if ui_logging_function:
		return ui_logging_function(message, waitForUserInput, offerAbort)
	else:
		DoPrint(message, waitForUserInput)
		return True
		
def RunGitCommand(args, wait=True, silent=False, printstdout=False, returnerrorcode=False, noWarningDialog=None, ignoreWhiteSpace=False, returnAbort=False):
	if noWarningDialog is None:
		noWarningDialog = not ShowWarningDialogByDefault

	primaryCommand = args[0]
	abort = False

	# Make a copy of args before modifying it
	args = args[:]

	if ignoreWhiteSpace:
		args.insert(1, "-Xignore-space-change")
	args.insert(0, "git.exe")

	# Check that index.lock doesn't exist. If it does ask the user to stop any other git processes or delete index.lock if it has been left behind
	index_lock_file = os.path.join(os.getcwd(), ".git", "index.lock")
	while os.path.isfile(index_lock_file):
		try:
			logging_function("index.lock file exists, which will probably make the next Git command fail.\nPlease check for running Git processes and, if there aren't any, press OK to delete it automatically:\n\n" + index_lock_file, waitForUserInput=True)
			os.remove(index_lock_file)
		except:
			pass

	output = ''
	success = 0 if returnerrorcode else False
	tryAgain = True
	numberOfTries = 0
	while tryAgain:
		tryAgain = False
		numberOfTries += 1
		try:
			if not silent:
				# Show the git command but way off to the right to keep the user-friendly messages segregated
				logging_function(("...->" + "\t" * 9) + "git " + " ".join(args))

			if wait:
				if printstdout:
					process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
					while process.poll() is None:
						line = process.stdout.readline()
						output += line
						if len(line) > 0:
							print line[:-1]
				else:
					output = subprocess.check_output(args, stderr=subprocess.STDOUT,shell=True)
			else:
				subprocess.Popen(args, stderr=subprocess.STDOUT,shell=True)
			if not returnerrorcode:
				success = True

		except subprocess.CalledProcessError, e:
			if returnerrorcode:
				logging_function("\nGit command returned error code {0} with output: {1}\n".format(e.returncode, e.output))
				success = e.returncode
			else:
				logging_function("\nGit command failed:\n")
				logging_function(e.output)
			output = e.output
			
			if silent:
				noWarning = True
			else:
				if noWarningDialog == "IfConflicts":
					# If there are conflicts, don't show a warning dialog or try again
					noWarning = HasConflicts()
				else:
					noWarning = noWarningDialog
		
			if not noWarning:
				highlightedErrorMessage = e.output.replace("error:", "***ERROR!!!***ERROR!!!***:")
				tryAgain = logging_function("Git command (" + " ".join(args) + ") failed! Please ask for help! You can either:\n\n" + 
																				" * If you press OK, it'll try again and you should keep an eye out for unexpected results\n" + 
																				" * If you press Abort, it'll skip this command and carry on - you should keep an eye out for unexpected results\n" + 
																				" * If you kill this program in Task Manager (GitUI.exe) and re-run it, it will offer to recover if you're in the middle of a pull\n\n'" + highlightedErrorMessage + "'", waitForUserInput=True, offerAbort=True)
				if not tryAgain:
					abort = True

				# More often that not, repeating a git command will be a better option than just skipping it. For example:
				#	* If it failed completely and didn't happen, e.g. due to index.lock existing
				#	* If it's a merge, fetch, push, pull, commit, status, reset, checkout, delete/create/rename branch, all of those are fine to simply repeat even if they succeed
				#	* Anything that is getting the status/log/etc. will be fine obviously
				#	* If it's a cherry-pick, and it did actually happen or partially happen, repeating it may conflict or may be fine but should draw attention to any problems
				# However, a failed "show" is likely because the file doesn't exist on a particular branch, so don't keep retrying those
				if primaryCommand == "show" and numberOfTries > 1:
					tryAgain = False
				continue

	if returnAbort:
		return success, output, abort
	return success, output

def RunGitCommandWithErrorCheck(command, errorstring, silent = False, printstdout=False):
	success, results = RunGitCommand(command, silent=silent, printstdout=printstdout, noWarningDialog=True) # errors handled below
	if not success:
		print results
		raise Exception(errorstring)
	if not silent and not printstdout:
		print results
	return results

# PrepareGitWorkingFolder is used by several TeamCity scripts to set up (or update) the local git repo on a TeamCity agent
def PrepareGitWorkingFolder(working_repo, branch=None, add_teamcity_remote=False, default_email=None, default_name=None):
	if not os.path.exists(working_repo):
		os.makedirs(working_repo)

	# Use GetBranch.py (with reverse lookup) to find the source branch
	sys.path.insert(1, os.path.normpath(os.path.join(__file__, "..", "..", "..", "Game", "Scripts", "Common")))
	from GetBranch import getBranch
	originBranch = getBranch(branch=branch, reverse=True)

	if not os.path.exists(os.path.join(working_repo, ".git")):
		RunGitCommandWithErrorCheck(["clone", "--verbose", "--no-checkout"] + (["--branch={0}".format(originBranch)] if originBranch is not None else []) + ["git@git.company.com:Project", working_repo], "Unable to clone into {0}".format(working_repo), printstdout=True)
	os.chdir(working_repo)

	# In case a previous Git operation was interrupted, delete the index.lock file
	indexLockFile = os.path.join(working_repo, ".git", "index.lock")
	if os.path.exists(indexLockFile):
		os.remove(indexLockFile)

	# New versions of git don't like having executables as git hooks, so delete the three Meandros git hooks (if their size suggests they are the exes).
	# The 'PushChangesBackToGit' script will re-create these properly.
	for hook in ['pre-commit', 'post-commit', 'pre-push']:
		hookPath = os.path.join(working_repo, ".git", "hooks", hook)
		if os.path.exists(hookPath) and os.path.getsize(hookPath) > 1024*1024:
			os.remove(hookPath)

	# Check that the user details exist for this repo, and set to some defaults if not
	if default_email is not None:
		success, email = RunGitCommand(["config", "--get", "user.email"], noWarningDialog=True)
		if not success:
			RunGitCommandWithErrorCheck(["config", "user.email", default_email], "Could not set user.email")
	if default_name is not None:
		success, name = RunGitCommand(["config", "--get", "user.name"], noWarningDialog=True)
		if not success:
			RunGitCommandWithErrorCheck(["config", "user.name", default_name], "Could not set user.name")
	if add_teamcity_remote:
		# Now add the teamcity remote, unless it already exists
		remotes = RunGitCommandWithErrorCheck(["remote"], "Can't get list of remotes").split('\n')[:-1]
		if not 'teamcity' in remotes:
			RunGitCommandWithErrorCheck(["remote", "add", "teamcity", "git@git.company.com:TeamCityProject"], "Can't add 'teamcity' remote")
		RunGitCommandWithErrorCheck(["fetch", "--verbose", "teamcity"] + (["teamcity/{0}".format(branch)] if branch is not None else []), "Could not fetch changes from teamcity", printstdout=True)

	# Ensure a clean working copy
	RunGitCommandWithErrorCheck(["reset", "--hard"], "Failed to hard reset")

	RunGitCommandWithErrorCheck(["fetch", "--verbose", "origin"] + ([originBranch] if branch is not None else []), "Could not fetch changes from origin", printstdout=True)

def CountParentsOfCommit(commit):
	success, output = RunGitCommand(["log", "--pretty=%P", "-n", "1", commit])
	return len(output.split(" "))
		
def DoPrint(output, waitForUserInput):
	if type(output)=='string' and output[0] == '&':
		print 'Working...'
	elif waitForUserInput==True:
		raw_input(output)
	else:
		print output

