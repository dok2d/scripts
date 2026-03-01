import ipaddress
import argparse

def read_ips_from_file(filename):
    """
    Читает IP-адреса и сети из файла, ожидая формат {ip}/{prefix} на каждой строке.
    """
    with open(filename, 'r') as file:
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
    ip_networks = sorted([ipaddress.ip_network(ip, strict=False) for ip in ip_list])
    aggregated_networks = list(ipaddress.collapse_addresses(ip_networks))
    expanded_networks = [force_expand_to_28(net) for net in aggregated_networks]
    unique_networks = sorted(set(expanded_networks))
    return unique_networks

def final_aggregation(networks):
    """
    Проводит финальное объединение итоговых сетей для создания самых крупных возможных подсетей.
    """
    # Применяем ipaddress.collapse_addresses для укрупнения еще раз
    return list(ipaddress.collapse_addresses(networks))

def aggressive_aggregation(networks):
    """
    Пытается агрессивно объединить сети в более крупные подсети, если они расположены последовательно
    и их можно объединить.
    """
    # Сортируем сети по начальным адресам
    networks = sorted(networks)
    merged_networks = []

    i = 0
    while i < len(networks):
        current_network = networks[i]
        
        # Попытка укрупнения сети
        while i + 1 < len(networks) and current_network.supernet_of(networks[i + 1]):
            current_network = current_network.supernet()
            i += 1

        merged_networks.append(current_network)
        i += 1

    return list(ipaddress.collapse_addresses(merged_networks))

def main():
    parser = argparse.ArgumentParser(description="Агрегирует IP-адреса и сети из файла в минимальные подсети")
    parser.add_argument("filename", help="Путь к файлу со списком IP-адресов и сетей в формате {ip}/{prefix}")
    args = parser.parse_args()
    filename = args.filename

    ip_list = read_ips_from_file(filename)
    unique_networks = aggregate_and_expand_ips(ip_list)
    final_networks = final_aggregation(unique_networks)
    
    # Финальное агрессивное объединение для создания самых крупных подсетей
    largest_networks = aggressive_aggregation(final_networks)

    print("Итоговый список минимальных подсетей после финального агрессивного объединения:")
    for net in largest_networks:
        print(net)

# Запуск основного скрипта
if __name__ == "__main__":
    main()
