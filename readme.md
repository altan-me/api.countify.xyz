pip install flask

docker build -t counter-api .

docker run -d -p 5000:5000 -v $(pwd)/data:/data counter-api
