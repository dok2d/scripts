import ipaddress
import argparse

def read_ips_from_file(filename):
    """
    Читает IP-адреса и сети из файла, ожидая формат {ip}/{prefix} на каждой строке.
    """
    with open(filename, 'r') as file:
        # Чтение строк и удаление пробелов
        return [line.strip() for line in file if line.strip()]

def force_expand_to_28(network):
    """
    Принудительно увеличивает подсеть до /28, если ее маска /29 или больше.
    """
    if network.prefixlen >= 29:
        return network.supernet(new_prefix=28)
    return network

def aggregate_and_expand_ips(ip_list):
    """
    Агрегирует сети, расширяет маленькие сети до /28 и удаляет дубликаты.
    """
    # Преобразуем строки в объекты ip_network, поддерживая разные маски
    ip_networks = sorted([ipaddress.ip_network(ip, strict=False) for ip in ip_list])

    # Объединяем IP-адреса в минимальные сети
    aggregated_networks = list(ipaddress.collapse_addresses(ip_networks))

    # Расширяем сети до /28, если они имеют меньшую маску
    expanded_networks = [force_expand_to_28(net) for net in aggregated_networks]
    
    # Убираем дубликаты и сортируем
    unique_networks = sorted(set(expanded_networks))
    return unique_networks

def final_aggregation(networks):
    """
    Проводит финальное объединение итоговых сетей для создания самых крупных возможных подсетей.
    """
    return list(ipaddress.collapse_addresses(networks))

def main():
    # Настройка аргументов командной строки
    parser = argparse.ArgumentParser(description="Агрегирует IP-адреса и сети из файла в минимальные подсети")
    parser.add_argument("filename", help="Путь к файлу со списком IP-адресов и сетей в формате {ip}/{prefix}")

    # Парсим аргументы
    args = parser.parse_args()
    filename = args.filename

    # Чтение IP-адресов и сетей из файла
    ip_list = read_ips_from_file(filename)

    # Первичное объединение и расширение IP-адресов и сетей
    unique_networks = aggregate_and_expand_ips(ip_list)

    # Финальное объединение для получения крупных подсетей
    final_networks = final_aggregation(unique_networks)

    # Вывод результата
    print("Итоговый список минимальных подсетей после финального объединения:")
    for net in final_networks:
        print(net)

# Запуск основного скрипта
if __name__ == "__main__":
    main()
