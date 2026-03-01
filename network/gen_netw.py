import ipaddress
import argparse

def read_ips_from_file(filename):
    """
    Читает IP-адреса из указанного файла, ожидая формат {ip}/32 на каждой строке.
    """
    with open(filename, 'r') as file:
        # Чтение строк и удаление пробелов
        return [line.strip() for line in file if line.strip()]

def force_expand_to_28(network):
    """
    Принудительно увеличивает подсеть до /28, если она имеет маску /29 или больше (например, /30, /31).
    """
    if network.prefixlen >= 29:
        return network.supernet(new_prefix=28)
    return network

def aggregate_and_expand_ips(ip_list):
    """
    Агрегирует IP-адреса, минимально расширяет сети и приводит малые сети к /28, если необходимо.
    """
    # Преобразуем строки в объекты ip_network
    ip_networks = sorted([ipaddress.ip_network(ip) for ip in ip_list])

    # Объединяем IP-адреса в минимальные сети
    aggregated_networks = list(ipaddress.collapse_addresses(ip_networks))

    # Расширяем сети до /28, если они имеют меньшую маску
    expanded_networks = [force_expand_to_28(net) for net in aggregated_networks]
    
    # Убираем дубликаты и сортируем
    unique_networks = sorted(set(expanded_networks))
    return unique_networks

def main():
    # Настройка аргументов командной строки
    parser = argparse.ArgumentParser(description="Агрегирует IP-адреса из файла в минимальные подсети")
    parser.add_argument("filename", help="Путь к файлу со списком IP-адресов в формате {ip}/32")

    # Парсим аргументы
    args = parser.parse_args()
    filename = args.filename

    # Чтение IP-адресов из файла
    ip_list = read_ips_from_file(filename)

    # Агрегация, расширение и сортировка IP-адресов
    unique_networks = aggregate_and_expand_ips(ip_list)

    # Вывод результата
    print("Список минимальных подсетей с учетом расширения до /28:")
    for net in unique_networks:
        print(net)

# Запуск основного скрипта
if __name__ == "__main__":
    main()
