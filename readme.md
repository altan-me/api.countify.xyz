# Counter API

The Counter API is a simple Flask application designed to track counts associated with unique IDs. It provides endpoints to get the total count, increment by one, and increase by a specified value. This document outlines the setup and usage of the Counter API.

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Installing

Clone the repository to your local machine and navigate to the directory containing the Dockerfile.

### Building the Docker Container

Build the Docker container for the Counter API using the following command:

```bash
docker-compose build
```

### Running the Application

Run the application using Docker with the following command:

```bash
docker-compose up
```

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
