# This script is run by TeamCity in order to maintain a flattened version of each branch that TeamCity needs to build.
# A bug in TeamCity means that merges cause tasks to run when no relevant changes were made: https://youtrack.jetbrains.com/issue/TW-24747
# The flattened version of the branch contains the same commit messages and authors, and similar contents. A commit with the Merge comment will resolve any conflicts.

# Parameters:
#	--previous-commit=<hash>		The previous commit that was pushed to the flattened branch. This can also be found by inspecting the TeamCity history, by passing 'tc' here, and passing the --tc-* parameters.
#	--tc-server=<url>				The URL of the TeamCity server (project-tc.rw), used to find the previous commit hash
#	--tc-buildid=<buildid>			The build configuration ID of the TeamCity configuration being used (ProjectContinuousBuilds_FlattenGit_FlattenGit), used to find the previous commit hash
#	--tc-username=<username>		The username to login with in TeamCity (%system.teamcity.auth.userId%), used to find the previous commit hash
#	--tc-password=<password>		The password to login with in TeamCity (%system.teamcity.auth.password%), used to find the previous commit hash
#	--current-commit=<hash>			The current commit that we're flattening up to.
#	--branch=<branch>				The name of the branch being flattened. "develop" will be flattened to "teamcity/develop"
#	--working-repo=<location>		The git repo to work within. e.g. E:\Flatten\Project

# This script works on a separate check out folder from the repo which it is running from. If this repo doesn't exist yet, it will start by creating it.
# In this separate check out folder, the following steps are taken:
#   ****
#	* if .git folder does not exist
#		* clone the repo into the folder: [git clone -n git@git.company.com:Project <working-repo>]
#	* if origin/teamcity/<branch> exists: [git ls-remote --heads | grep -sw "refs/heads/teamcity/<branch>"]
#		* check out the current version of origin/teamcity/<branch>: [git checkout -B teamcity/<branch> origin/teamcity/<branch>]
#	* else
#		* create branch origin/teamcity/<branch> based on the HEAD of origin/<branch> [git checkout -b teamcity/<branch> origin/<branch> --no-track]
#	****
#   * list all revisions between <previous-commit> and <current-commit>: [git log <previous-commit>..<current-commit> --format=format:%H]
#	* for each revision in the reverse of this list:
#		* if the revision has more than 1 (generally 2) parents, it is a merge. [GitFunctions.CountParentsOfCommit(revision) > 1]
#			* if this is the LAST merge:
#				* record the current revision, to return to in a sec.
#				* hard reset to this revision in the source branch [git reset --hard <revision>]
#				* soft reset back to the previously recorded revision [git reset --soft <recorded-revision>]
#				* retrieve commit message and author information from the revision [git log -n 1 <revision> --format="\"%s\" \"%an\""]
#				* commit local changes. [git commit -m "<message>" --author='<author>']
#		* if there was only one parent:
#			* cherry pick the change with 'accept theirs' as the default [git cherry-pick --strategy=recursive --strategy-option=theirs <revision>]
#	* push everything [git push]

import sys
import os
import re
from GitFunctions import RunGitCommand, RunGitCommandWithErrorCheck, CountParentsOfCommit, PrepareGitWorkingFolder
import argparse
import tempfile
import urllib2
import base64
from xml.etree import ElementTree

def ModifyLastCommitMessage(source_branch, revision):
	# Modify the last commit message to include the source branch/revision.
	head = RunGitCommandWithErrorCheck(["log", "-n", "1", "HEAD", "--format=%H"], "Failed to retrieve current head revision").replace('\n','')
	message = RunGitCommandWithErrorCheck(["log", "-n", "1", head, '--format=%s'], "Failed to retrieve commit message for revision {0}".format(revision))
	with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
		f.write(message)
		# Write out the source revision number, for TeamCity to use to label the build, etc.
		f.write('\nbranch: {0}, revision: {1}\n\n'.format(source_branch, revision))
		temp = f.name
	RunGitCommandWithErrorCheck(["commit", "--amend", "--file={0}".format(temp)], "Failed to amend commit message to include source branch and revision")

def FlattenGit(current_commit, branch, working_repo, max_commits_to_cherry_pick=100):
	# Before doing ANYTHING else git-related, disable the auto Garbage Collection. This takes ages to do nothing, and this is a good way of getting this setting onto all of the TeamCity agents.
	RunGitCommand(["config", "--global", "gc.auto", "0"])

	source_branch = branch
	dest_branch = "teamcity/"+branch
	previous_commit = None

	# First, prepare the working folder, which is a git repo
	PrepareGitWorkingFolder(working_repo, branch=source_branch, add_teamcity_remote=True, default_email="noreply@company.com", default_name="TeamCity")

	# Abort any active cherry-picks
	RunGitCommand(["cherry-pick", "--abort"])

	heads = RunGitCommandWithErrorCheck(["ls-remote", "--heads", "teamcity"], "Unable to list remote heads")
	# Check whether the destination branch already exists on the remote.
	if  "\trefs/heads/teamcity/{0}\n".format(branch) in heads:
		# Checkout the current state of the destination branch, over-writing any existing local branch of the same name
		RunGitCommandWithErrorCheck(["checkout", "-B", dest_branch, "teamcity/"+dest_branch], "Checkout failed")
		# Get the latest commit message of this branch, in order to get the correct previous commit hash. Note that there are now >1 Flatten Git jobs, so this is the only reliable method.
		commitMessage = RunGitCommandWithErrorCheck(["log", "-1", "--pretty=%B"], "Log failed")
		print "Previous commit message:"
		print commitMessage
		p = re.compile("revision: ([a-f0-9]+)")
		revisions = p.findall(commitMessage)
		numFound = len(revisions)
		if numFound > 0:
			previous_commit = revisions[numFound-1]

	revisions = None
	if previous_commit is not None:
		# Check whether the previous revision is actually an ancestor of the current revision.
		(code,output) = RunGitCommand(["merge-base", "--is-ancestor", previous_commit, current_commit], returnerrorcode=True)
		if code == 0:
			# The current commit is indeed a descendent of the previous commit
			# Now get the list of revisions that we need to apply
			revisionList = RunGitCommandWithErrorCheck(["log", "{0}..{1}".format(previous_commit, current_commit), "--format=%H"], "Failed to retrieve log of changes between {0} and {1}".format(previous_commit, current_commit))
			revisions = revisionList.split('\n')[:-1]
			# Limit the number of revisions to cherry-pick. Above a certain amount, no time is saved by cherry-picking to a flat branch, as the cherry-picking can take too long
			num_commits = len(revisions)
			if num_commits > max_commits_to_cherry_pick:
				print "{0} commits is too many to cherry pick".format(num_commits)
				revisions = None
			else:
				print "{0} (<{1}) commits to cherry pick".format(num_commits, max_commits_to_cherry_pick)

	if revisions is None:
		# The remote branch does not exist, or so checkout the previous commit, and push it to the remote as the destination branch
		RunGitCommandWithErrorCheck(["checkout", "-B", dest_branch, current_commit, "--no-track"], "Checkout failed")
		# Amend last commit message to include the source branch and commit
		ModifyLastCommitMessage(source_branch, current_commit)
		# Force-push, in case the branch already existed (which will be the case if there were too many commits)
		RunGitCommandWithErrorCheck(["push", "--force", "--set-upstream", "teamcity", dest_branch], "Failed to set upstream branch")

	# If there is nothing to do, just return, as TeamCity would ignore empty commits.
	if revisions is None or len(revisions) == 0:
		return

	# Find the last merge revision (this list is about to be reversed, so this is the first one found in the list)
	lastMerge = None
	for revision in revisions:
		if CountParentsOfCommit(revision) > 1:
			lastMerge = revision
			break
	lastCommit = revisions[0]
	# Go through the list of revisions, applying each one to dest_branch
	revisions.reverse()
	commitsMade = False
	for revision in revisions:
		resolveToRevision = False
		# Check whether this is a merge. If it is the last merge, resolve all changes to this revision. All other merges are ignored.
		if CountParentsOfCommit(revision) > 1:
			# If this is the last merge, do something, otherwise, do nothing
			if revision == lastMerge:
				resolveToRevision = True
			else:
				continue
		# On the last commit in the list, also resolve this way, to ensure that the result is the same as the source branch
		if revision == lastCommit:
			resolveToRevision = True
		if resolveToRevision:
			# Get the current head commit, to reset back to
			head = RunGitCommandWithErrorCheck(["log", "-n", "1", "HEAD", "--format=%H"], "Failed to retrieve current head revision").replace('\n','')
			# Hard reset to the merge commit.
			RunGitCommandWithErrorCheck(["reset", "--hard", revision], "Failed to reset to source branch")
			# Soft reset back to the destination branch
			RunGitCommandWithErrorCheck(["reset", "--soft", head], "Failed to reset back to destination branch")
			# Commit the index (unless it is empty, except for the last commit).
			filesInIndex = False
			fileList = RunGitCommandWithErrorCheck(["status", "--porcelain"], "Failed to check the index")
			files = fileList.split('\n')[:-1]
			# If a line starts with an 'A', 'M' or 'D' then a file has been added, modified or deleted in the index.
			# If it starts with a space, then it has been added or deleted but not in the index.
			# If it starts with '??' then it is a new file that has not been added.
			for file in files:
				if file[0] != ' ' and file[0] != '?':
					filesInIndex = True
			if filesInIndex:
				# Retrieve the commit message and author details
				message = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%s'], "Failed to retrieve commit message for revision {0}".format(revision))
				author = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%an'], "Failed to retrieve commit author for revision {0}".format(revision)).replace('\n', '')
				email = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%ae'], "Failed to retrieve commit author email for revision {0}".format(revision)).replace('\n', '')
				# Write the message out to a file, to retain any linebreaks or quotation marks
				with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
					f.write(message)
					temp = f.name
				RunGitCommandWithErrorCheck(["commit", "--author='{0} <{1}>'".format(author, email), "--file={0}".format(temp)], "Failed to commit flattened merge")
				os.remove(temp)
				commitsMade = True
		else:
			# Only one parent, and not the last commit, so cherry-pick this revision, using conflict-resolution strategy "theirs".
			# If any conflicts actually arose, they will be resolved later by the last merge.
			# This call may fail if the commit made no changes, so do not use RunGitCommandsWithErrorCheck
			success, output = RunGitCommand(["cherry-pick", "--allow-empty", "--strategy=recursive", "--strategy-option=theirs", revision])
			if not success:
				# It could be that "DU" (deleted/unresolved) changes exist. A file was deleted on ours and modified on theirs. Find all such files and resolve manually by deleting
				status = RunGitCommandWithErrorCheck(["status", "--porcelain"], "Cannot retrieve status after failed cherry-pick").split('\n')[:-1]
				resolvedDeletedFiles = False
				otherUnresolvedFiles = False
				for fileStatus in status:
					if fileStatus[1] == 'U':
						if fileStatus[0] == 'D':
							if not resolvedDeletedFiles:
								print "Resolving deleted files:"
							RunGitCommandWithErrorCheck(["rm", fileStatus[3:]], "Failed to remove {0}".format(fileStatus[3:]))
							resolvedDeletedFiles = True
						else:
							otherUnresolvedFiles = True
				if otherUnresolvedFiles:
					print "WARNING: Could not resolve some files. Check output:"
					print output
				if resolvedDeletedFiles:
					# Retrieve the commit message and author details
					message = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%s'], "Failed to retrieve commit message for revision {0}".format(revision))
					author = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%an'], "Failed to retrieve commit author for revision {0}".format(revision)).replace('\n', '')
					email = RunGitCommandWithErrorCheck(["log", "-n", "1", revision, '--format=%ae'], "Failed to retrieve commit author email for revision {0}".format(revision)).replace('\n', '')
					# Write the message out to a file, to retain any linebreaks or quotation marks
					with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
						f.write(message)
						temp = f.name
					(success, output) = RunGitCommand(["commit", "--author='{0} <{1}>'".format(author, email), "--file={0}".format(temp)])
					if not success:
						print "Failed to commit resolved cherry-pick"
						RunGitCommandWithErrorCheck(["cherry-pick", "--abort"], "Failed to abort failed cherry-pick")
					os.remove(temp)
			if success:
				commitsMade = True
			else:
				print "WARNING: Failed to cherry pick revision {0}; check output below. This is probably due to the same commit existing in multiple branches that were merged together.".format(revision)
				# Assume that this failed because that change was already in this branch (which would be sorted out by a subsequent merge) so print output for logging and reset
				print output
				RunGitCommandWithErrorCheck(["cherry-pick", "--abort"], "Failed to abort cherry-pick", printstdout=True)
	
	# Only push this if there were some changes to push
	if commitsMade:
		ModifyLastCommitMessage(source_branch, revision)
	
		# Finally, push any pending changes.
		RunGitCommandWithErrorCheck(["push", "teamcity", dest_branch], "Failed to push")


def main():
	parser = argparse.ArgumentParser(description='Maintain a flattened version of a development branch in Git.')
	parser.add_argument('--current-commit', default=None, help='The current commit that we are flattening up to.')
	parser.add_argument('--branch', default='develop', help='The development branch (e.g. develop) that we are flattening')
	parser.add_argument('--working-repo', default=r'E:\Flatten\Project', help='The disk location of the working repo. This should be separate to any active build folders.')
	parser.add_argument('--tc-server', default='', help='The URL of the TeamCity server (project-tc.rw), used to find the previous commit hash.')
	parser.add_argument('--tc-buildid', default='', help='The build configuration ID of the TeamCity configuration being used (ProjectContinuousBuilds_FlattenGit_FlattenGit), used to find the previous commit hash')
	parser.add_argument('--tc-username', default='', help='The username to login with in TeamCity, used to find the previous commit hash')
	parser.add_argument('--tc-password', default='', help='The password to login with in TeamCity, used to find the previous commit hash')
	parser.add_argument('--maxCommitsToCherryPick', default=100, type=int, help='The maximum number of commits to cherry pick. If exceeded, the TeamCity branch will just be a copy of the source branch (with the last commit modified to include the source commit and branch)')
	args = parser.parse_args()

	FlattenGit(args.current_commit, args.branch, args.working_repo, args.maxCommitsToCherryPick)

if __name__ == "__main__":
    main()
