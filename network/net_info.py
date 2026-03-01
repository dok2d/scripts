import ipaddress
import argparse

def calculate_subnet_info(subnet, specific_value=None):
    net = ipaddress.ip_network(subnet, strict=False)
    min_address = net.network_address
    max_address = net.broadcast_address - 1
    available_addresses = net.num_addresses - 2  # исключаем адрес сети и широковещательный адрес

    if specific_value == 'min':
        return str(min_address)
    elif specific_value == 'max':
        return str(max_address)
    elif specific_value == 'count':
        return available_addresses
    else:
        return {
            'min_address': str(min_address),
            'max_address': str(max_address),
            'available_addresses': available_addresses
        }

def main():
    parser = argparse.ArgumentParser(description='Calculate subnet information.')
    parser.add_argument('subnet', type=str, help='Subnet in CIDR format (e.g., 10.28.52.0/27)')
    parser.add_argument('--value', type=str, choices=['min', 'max', 'count'], help='Specific value to return')

    args = parser.parse_args()

    result = calculate_subnet_info(args.subnet, args.value)

    if args.value:
        print(result)
    else:
        print(f"Минимальный адрес: {result['min_address']}")
        print(f"Максимальный адрес: {result['max_address']}")
        print(f"Количество доступных адресов: {result['available_addresses']}")

if __name__ == '__main__':
    main()
