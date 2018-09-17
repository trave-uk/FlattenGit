# Helper script to get the current branch in Git and set the 'branch' env-var to its name
# This tries to find the TeamCity branch that this branched from, if the branch is not built on TeamCity

import os
import sys
import json
import re
import argparse
import traceback

def getBranch(brief=False, branch=None, reverse=False):
	if branch is None:
		if 'gitbranch' in os.environ:
			# If this is run from within TeamCity, then there is no local git repo and the %gitbranch% envvar should be set
			return os.environ['gitbranch']
		from GitFunctions import RunGitCommand, RunGitCommandWithErrorCheck, CountParentsOfCommit, PrepareGitWorkingFolder
		# This returns the upstream branch (tracked branch)
		success, remoteBranch = RunGitCommand(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'], silent=brief)
		if success:
			remoteBranch = remoteBranch.replace('\n','')
			if not brief:
				print 'Remote branch is {0}.'.format(remoteBranch)
			# Trim remote name 
			branch = remoteBranch[remoteBranch.find('/')+1:]
		else:
			success, branch = RunGitCommand(['symbolic-ref', '--short', 'HEAD'], silent=brief)
			if success:
				branch = branch.replace('\n','')
				if not brief:
					print 'No upstream branch found so using local branch name, {0}'.format(branch)
			else:
				if not brief:
					print 'No local or remote branch name found. Defaulting to develop'
				branch = 'develop'

	if not brief:
		print 'Searching for branch "{0}"'.format(branch)

	# Having got hold of a branch name, look it up in the FlattenGit vcs repo on TeamCity
	# https://teamcity.company.com/app/rest/vcs-roots/id:GitFlatten

	# Use the 'PackageManager.py' script to ensure that requests is installed
	import PackageManager
	PackageManager.ensurePackage('requests', silent=brief)
	import requests

	try:
		r = requests.get('https://teamcity.company.com/app/rest/vcs-roots/id:GitFlatten', headers = {'Accept': 'application/json', 'Authorization': 'Basic $$$ENCODED-AUTH$$$'})
	except Exception as e:
		# Report error and re-raise the exception
		print "Exception raised contacting https://teamcity.company.com/. Python upgrade (to 2.7.13) should fix this."
		raise(e)
	if r.status_code != 200:
		if r.status_code == 404:
			if not brief:
				print "GitFlatten vcs root not found"
			return branch
		else:
			r.raise_for_status()
	vcsRoot = json.loads(r.text)
	# The vcs root contains properties, including "teamcity:branchSpec", which contains the mapping we're looking for
	properties = vcsRoot['properties']
	newBranch = None
	for property in properties['property']:
		if property['name'] == 'teamcity:branchSpec':
			branchSpec = property['value']
			branches = branchSpec.split('\n')
			activeBranchPrefix = '+:refs/heads/'
			# activeBranches will contain a list like ['(develop)', '(beta*)', '(alpha10.6.6)', '(alpha10.6.7)', '(alpha10.6.8)', 'features/(deathcon)sequences', 'feature/wpg/(props)']
			activeBranches = [line[len(activeBranchPrefix):] for line in branches if line.startswith(activeBranchPrefix)]
			for activeBranch in activeBranches:
				if reverse:
					# For a reverse look-up, we extract the text within the brackets and see if it matches the branch.
					useGroup = '(' in activeBranch and ')' in activeBranch
					if useGroup:
						(start,rest) = activeBranch.split('(')
						(middle,end) = rest.split(')')
						regex = middle.replace('.', '\\.')
					else:
						regex = activeBranch.replace('.','\\.').replace('*','.*')
					p = re.compile(regex)
					result = p.search(branch)
					if result is not None:
						if useGroup:
							# Replace the bracketted part of activeBranch with branch
							newBranch = start + branch + end
						else:
							# The branch matches exactly, so return it.
							newBranch = branch
						if not brief:
							print "TeamCity branch '{0}' maps to '{1}'".format(branch, newBranch)
						branch = newBranch
						break
				else:
					# Convert the spec to a regex by escaping '.' and converting '*' to '.*'. The brackets will define a group in the regex.
					regex = activeBranch.replace('.','\\.').replace('*','.*')
					p = re.compile(regex)
					result = p.search(branch)
					if result is not None:
						groups = len(result.groups())
						if groups < 1:
							newBranch = result.group()
						else:
							newBranch = result.group(1)
						if not brief:
							print "Branch '{0}' maps to '{1}' on TeamCity".format(branch, newBranch)
						branch = newBranch
						break
			break
	if newBranch is None:
		if reverse:
			if not brief:
				print "No branch found for TeamCity branch '{0}'".format(branch)
		elif os.path.exists( os.path.join(r"X:\CurrentProjects", "ProjectV", "Game", branch)):
			if not brief:
				print "No TeamCity branch found, but a '{0}' build folder exists".format(branch)
		else:
			if not brief:
				print "No TeamCity branch found for '{0}'.".format(branch)

			# As a last resort, attempt to use FindMatchingBuild to determine a branch
			sys.path.insert(1, os.path.normpath(os.path.join(os.environ['MEANDROS_DATABASE_PATH'], 'Scripts', 'TeamCity')))
			from FindMatchingBuild import FindMatchingBuild
			# buildFolder will be (e.g.) 'X:\CurrentProjects\ProjectV\Game\develop\Build_12345_*_*_*_*'
			buildFolder = FindMatchingBuild()
			if buildFolder == "No matching build found":
				branch = 'develop'
				if not brief:
					print "No matching TeamCity branches found(!) Resorting to develop"
			else:
				branch = os.path.split(os.path.split(buildFolder)[0])[1]
				if not brief:
					print "Nearest branch found on TeamCity is {0}".format(branch)

	return branch


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Return a mapping between a TeamCity branch name and a dev branch name.')
	parser.add_argument('--brief', action='store_true', help='Minimize stdout output to the result, with none of the working. NB: stderr may have output if (for example) requests is installed.')
	parser.add_argument('--branch', default=None, help='The branch to search for. If not set, use the current remote (or local) branch name, unless %gitbranch% is set, in which case return that.')
	parser.add_argument('--reverse', action='store_true', help='If set, reverse the look-up: a TeamCity branch name is given, and the actual branch name is returned')
	args = parser.parse_args()
	branch = getBranch(brief = args.brief, branch = args.branch, reverse = args.reverse)
	if args.brief:
		print branch
	else:
		print "Using branch '{0}'".format(branch)
