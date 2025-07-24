from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    details = None
    if request.method == "POST":
        github_link = request.form.get("github_link")

        # For demo, fake logic:
        if "correct" in github_link.lower():
            result = "Code is correct "
        else:
            result = "Code is NOT correct "
            details = [
                "Update README file.",
                "Fix linting issues.",
                "Add unit tests for new functionality."
            ]

    return render_template("index.html", result=result, details=details)

if __name__ == "__main__":
    app.run(debug=True)
