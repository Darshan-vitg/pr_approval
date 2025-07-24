import os
import json
import boto3
import requests
import hcl2
from flask import Flask, render_template, request
from io import StringIO
from urllib.parse import urlparse

app = Flask(__name__)

# Update this to your actual Claude model if needed
CLAUDE_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
AWS_REGION = "us-east-1"

# Claude Bedrock call
def call_claude(prompt):
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.5,
        "top_p": 0.9,
        "stop_sequences": []
    }

    try:
        response = client.invoke_model(
            modelId=CLAUDE_MODEL_ID,  # e.g., "anthropic.claude-3-5-sonnet-20240620-v1:0"
            body=json.dumps(body),
            contentType="application/json"
        )
        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text']
    except Exception as e:
        return f"❌ Claude API error: {str(e)}"

# GitHub utility
def extract_repo_info(github_link):
    parsed_url = urlparse(github_link)
    path_parts = parsed_url.path.strip("/").split("/")
    if len(path_parts) < 2:
        return None, None
    return path_parts[0], path_parts[1].replace(".git", "")

def get_pull_files(owner, repo, pull_number):
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/files"
    response = requests.get(url)
    return response.json() if response.status_code == 200 else []

def get_file_content(owner, repo, sha):
    url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}"
    headers = {"Accept": "application/vnd.github.v3.raw"}
    response = requests.get(url, headers=headers)
    return response.text if response.status_code == 200 else ""

# AWS config gatherer
def fetch_aws_config():
    try:
        ec2 = boto3.client("ec2", region_name=AWS_REGION)
        s3 = boto3.client("s3", region_name=AWS_REGION)
        iam = boto3.client("iam", region_name=AWS_REGION)
        lambda_client = boto3.client("lambda", region_name=AWS_REGION)
        apigw = boto3.client("apigatewayv2", region_name=AWS_REGION)

        vpcs = ec2.describe_vpcs().get("Vpcs", [])
        subnets = ec2.describe_subnets().get("Subnets", [])
        security_groups = ec2.describe_security_groups().get("SecurityGroups", [])
        route_tables = ec2.describe_route_tables().get("RouteTables", [])
        instances = ec2.describe_instances().get("Reservations", [])

        buckets = s3.list_buckets().get("Buckets", [])
        bucket_names = [b["Name"] for b in buckets]

        roles = iam.list_roles().get("Roles", [])
        users = iam.list_users().get("Users", [])
        functions = lambda_client.list_functions().get("Functions", [])
        apis = apigw.get_apis().get("Items", [])

        return {
            "ec2": {
                "vpcs": [v["VpcId"] for v in vpcs],
                "subnets": [s["SubnetId"] for s in subnets],
                "security_groups": [sg["GroupId"] for sg in security_groups],
                "route_tables": [rt["RouteTableId"] for rt in route_tables],
                "instances": [i["Instances"][0]["InstanceId"] for i in instances if i["Instances"]],
            },
            "s3": bucket_names,
            "iam": {
                "roles": [r["RoleName"] for r in roles],
                "users": [u["UserName"] for u in users],
            },
            "lambda": [f["FunctionName"] for f in functions],
            "apigateway": [a["Name"] for a in apis]
        }
    except Exception as e:
        return {"error": str(e)}

# Flask route
@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    details = []
    if request.method == "POST":
        github_link = request.form["github_link"].strip()
        if "/pull/" not in github_link:
            result = "❌ Please enter a valid GitHub PR link."
            return render_template("index.html", result=result)

        owner, repo = extract_repo_info(github_link)
        pull_number = github_link.split("/pull/")[1].split("/")[0]

        files = get_pull_files(owner, repo, pull_number)
        aws_data = fetch_aws_config()

        print("\n===== AWS Live Config =====")
        print(json.dumps(aws_data, indent=2))

        terraform_blocks = []
        file_contents = []

        for f in files:
            if f["filename"].endswith(".tf"):
                content = get_file_content(owner, repo, f["sha"])
                print(f"\n===== Terraform File: {f['filename']} =====")
                print(content)

                file_contents.append(f"--- {f['filename']} ---\n{content}")
                try:
                    parsed = hcl2.load(StringIO(content))
                    terraform_blocks.append({
                        "file": f["filename"],
                        "resources": parsed.get("resource", {})
                    })
                except Exception as e:
                    print(f"[ERROR parsing {f['filename']}] {e}")
                    terraform_blocks.append({
                        "file": f["filename"],
                        "error": str(e),
                        "raw_snippet": content[:500]
                    })

        print("\n===== Parsed Terraform Blocks =====")
        print(json.dumps(terraform_blocks, indent=2))

        # Claude Prompt
        prompt = (
                "You are an expert DevSecOps engineer.\n\n"
                "Your task is to review Terraform code from a GitHub Pull Request (PR), and compare it to the current AWS infrastructure.\n"
                "You will be provided:\n"
                "1. The current live AWS config (retrieved via boto3).\n"
                "2. Terraform blocks extracted from the PR.\n"
                "3. Full Terraform file contents.\n\n"
                "Please analyze and return:\n"
                "- ✅ A final decision at the top: APPROVE or REJECT the PR.\n"
                "- A one-sentence justification for the decision.\n"
                "- If the PR is REJECTED, provide a detailed report on:\n"
                "  • Resource mismatches between Terraform and live AWS config\n"
                "  • Security or configuration issues\n"
                "  • Violations of infrastructure best practices\n"
                "  • Any improvement suggestions\n\n"
                "❗️The Terraform code must match the live AWS config. If there is any mismatch, the PR should be rejected.\n\n"
                "=== AWS CONFIG ===\n"
                f"{json.dumps(aws_data, indent=2)}\n\n"
                "=== PARSED TERRAFORM BLOCKS ===\n"
                f"{json.dumps(terraform_blocks, indent=2)}\n\n"
                "=== FULL TERRAFORM FILES ===\n"
                + "\n\n".join(file_contents) + "\n\n"
                                               "Please start your response with either:\n"
                                               "✅ PR APPROVED: <short reason>\n"
                                               "OR\n"
                                               "❌ PR REJECTED: <short reason>\n"
        )

        ai_response = call_claude(prompt)
        result = ai_response.split("\n")[0].strip()
        details = ai_response.split("\n")[1:]
        print("\n===== Claude Response =====")
        print(ai_response)

    return render_template("index.html", result=ai_response)

if __name__ == "__main__":
    app.run(debug=True)







# from flask import Flask, request
# import os
# import boto3
# import json
# import requests
# from github_utils import get_pr_diff, get_full_file_content
#
# app = Flask(__name__)
#
# GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
#
# bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
# CLAUDE_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
#
# def fetch_aws_config():
#     ec2 = boto3.client("ec2", region_name=AWS_REGION)
#     s3 = boto3.client("s3", region_name=AWS_REGION)
#
#     try:
#         vpcs = ec2.describe_vpcs().get("Vpcs", [])
#         buckets = s3.list_buckets().get("Buckets", [])
#
#         bucket_details = []
#         for bucket in buckets:
#             name = bucket["Name"]
#             try:
#                 loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
#                 acl = s3.get_bucket_acl(Bucket=name)
#                 bucket_details.append({
#                     "Name": name,
#                     "Location": loc,
#                     "ACL": acl
#                 })
#             except Exception as e:
#                 bucket_details.append({"Name": name, "Error": str(e)})
#
#         return {
#             "vpcs": vpcs,
#             "buckets": bucket_details
#         }
#     except Exception as e:
#         return {"error": str(e)}
#
# @app.route('/')
# def home():
#     return '''
#     <h2>Auto PR Approver with AWS Validation (Claude 3.5 via Bedrock)</h2>
#     <form action="/review-pr" method="POST">
#         <label>GitHub PR URL: <input name="pr_url" required></label><br>
#         <button type="submit">Submit</button>
#     </form>
#     '''
#
# @app.route('/review-pr', methods=['POST'])
# def review_pr():
#     pr_url = request.form['pr_url']
#     try:
#         parts = pr_url.strip().split('/')
#         owner, repo, pr_number = parts[3], parts[4], parts[-1]
#
#         files = get_pr_diff(owner, repo, pr_number, GITHUB_TOKEN)
#         if not files:
#             return "❌ Error: Failed to fetch PR files. Check your GitHub token, repo URL, or permissions."
#
#         file_contents = []
#         for f in files:
#             filename = f["filename"]
#             sha = f.get("sha")
#             if sha:
#                 content = get_full_file_content(owner, repo, sha, GITHUB_TOKEN)
#                 file_contents.append(f"--- {filename} ---\n{content}")
#         full_code = "\n\n".join(file_contents)
#
#         aws_data = fetch_aws_config()
#
#         prompt = (
#             "You are an expert DevSecOps engineer. Review the following pull request for any security risks, "
#             "AWS configuration issues, or incorrect usage. Base your review on this AWS context and approve only if safe.\n\n"
#             f"AWS Context:\n{json.dumps(aws_data, indent=2)}\n\n"
#             f"PR Files:\n{full_code}"
#         )
#
#         response = bedrock.invoke_model(
#             modelId=CLAUDE_MODEL_ID,
#             contentType="application/json",
#             accept="application/json",
#             body=json.dumps({
#                 "anthropic_version": "bedrock-2023-05-31",
#                 "messages": [
#                     {"role": "user", "content": prompt}
#                 ],
#                 "max_tokens": 1000,
#                 "temperature": 0.3
#             })
#         )
#
#         response_body = json.loads(response["body"].read())
#         ai_opinion = response_body['content'][0]['text']
#
#         if "approve" in ai_opinion.lower():
#             approve_pr(owner, repo, pr_number)
#             result = "✅ PR Approved by Claude 3.5 + AWS"
#         else:
#             result = f"❌ Claude Review Failed:\n{ai_opinion}"
#
#         output = "<pre>" + result + "\n\n"
#         output += "---- AWS CONFIGURATION ----\n" + json.dumps(aws_data, indent=2) + "\n\n"
#         output += "---- GITHUB PR FILE CONTENTS ----\n" + full_code + "</pre>"
#         return output
#
#     except Exception as e:
#         return f"Error: {str(e)}"
#
# def approve_pr(owner, repo, pr_number):
#     url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
#     headers = {
#         "Authorization": f"token {GITHUB_TOKEN}",
#         "Accept": "application/vnd.github+json"
#     }
#     data = {
#         "body": "Auto-approved by Claude 3.5 and AWS checks",
#         "event": "APPROVE"
#     }
#     requests.post(url, headers=headers, json=data)
#
# if __name__ == "__main__":
#     app.run(debug=True)
