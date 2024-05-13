🔒👾 Here's a README file for your Flask-based Counter API, complete with setup instructions, usage guidelines, and examples. This README provides clarity on installation, building, and running your Docker container, as well as how to interact with your API.

---

# Counter API

The Counter API is a simple Flask application designed to track counts associated with unique IDs. It provides endpoints to get the total count, increment by one, and increase by a specified value. This document outlines the setup and usage of the Counter API.

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

You need Python and Docker installed on your system. You can install Flask using pip:

```bash
pip install flask
```

### Installing

Clone the repository to your local machine and navigate to the directory containing the Dockerfile.

### Building the Docker Container

Build the Docker container for the Counter API using the following command:

```bash
docker build -t counter-api .
```

### Running the Application

Run the application using Docker with the following command:

```bash
docker run -d -p 5000:5000 -v $(pwd)/data:/data counter-api
```

This command will start the Counter API in a detached mode, map port 5000 of the container to port 5000 on your host, and mount the `./data` directory on the host to `/data` in the container for persistent storage.

## API Usage

The Counter API provides the following endpoints:

### Get Total Count

- **Endpoint**: `GET /get-total/{id}`
- **Description**: Retrieves the current total count for the specified ID. If the ID does not exist, it initializes it with a total of 0.
- **Example**: [https://api.countify.xyz/get-total/testID](https://api.countify.xyz/get-total/testID)

### Increment Count by One

- **Endpoint**: `POST /increment/{id}`
- **Description**: Increments the total count associated with the specified ID by 1. If the ID does not exist, it initializes it with a total of 1.
- **Example**: [https://api.countify.xyz/increment/testID](https://api.countify.xyz/increment/testID)

### Increase Count by a Specified Value

- **Endpoint**: `POST /increase/{id}`
- **Description**: Increases the total count associated with the specified ID by the value provided in the JSON payload. If the ID does not exist, it initializes it with the specified total.
- **Payload**:
  ```json
  {
    "value": 50
  }
  ```
- **Example**: [https://api.countify.xyz/increase/testID](https://api.countify.xyz/increase/testID)

## Contributing

Please read [CONTRIBUTING.md](#) for details on our code of conduct, and the process for submitting pull requests to us.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

---
